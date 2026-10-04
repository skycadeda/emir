"""Run agents on the eval task cards and grade them.

    python agent/evals/run.py --list
    python agent/evals/run.py --prompt-stdin \\
        --agent-cmd 'claude -p --output-format stream-json --verbose --allowedTools Bash,Read' \\
        [--cards screen-dc,troubleshoot-e417] [--out DIR] [--timeout 900] [--jobs 1]
    python agent/evals/run.py --agent-cmd 'codex exec --full-auto'
    python agent/evals/run.py --mcp --prompt-stdin --agent-cmd \\
        'claude -p --output-format stream-json --verbose --mcp-config {mcp_config} ...'

Claude Code's `--allowedTools`, `--tools` and `--mcp-config` take several
values: a prompt appended after them is read as one more, so give the prompt
on stdin (`--prompt-stdin`) whenever the agent command ends with one.

For each card it

1. makes a workspace: a copy of this repository's tracked files without
   `agent/evals/` (the graders hold the answers), with the card's fixtures built
   into `inputs/`;
2. puts a logging shim first on PATH as `emir` (and as the copy's `bin/emir`):
   every emir call the agent makes is recorded in `.eval/calls.jsonl`, then run
   by the real emir;
3. runs the agent command in the workspace with the card's prompt as its last
   argument (`--prompt-stdin` sends it on stdin instead), under a timeout. With
   `--mcp` the agent drives emir over MCP: `{mcp_config}` in the agent command
   becomes the path of an MCP client file that starts `emir mcp`, and the tool
   calls of its transcript are graded as emir calls;
4. grades the workspace (`grade.py`) and writes `results.json` and a table.

The agent command is any CLI that takes a prompt and works in the current
directory: `claude -p ...`, `codex exec ...`, or `python agent/evals/fake_agent.py`
(the scripted agent the tests use). With `claude -p --output-format json` or
`stream-json` the runner also records the cost, the time and the number of turns,
and with `stream-json --verbose` the shell commands of the transcript.

Exit status: 0 when every card graded passed, 1 otherwise, 2 on a usage error.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fixtures  # noqa: E402
import grade  # noqa: E402

CARDS_DIR = os.path.join(HERE, "cards")

#: appended to every card's prompt: where things are and where the answer goes
SUFFIX = """

(Your working directory is a copy of the emir repository. The person's files are in ./inputs/.
Use the `emir` command on PATH. When you are done, write your answer to the person -- the message
you would send them -- to ./answer.md, and print it too.)"""

#: the same, for an agent that drives emir through its MCP server
SUFFIX_MCP = """

(Your working directory is a copy of the emir repository. The person's files are in ./inputs/.
Use the emir MCP tools (emir_*): they run emir for you. When you are done, write your answer to the
person -- the message you would send them -- to ./answer.md, and print it too.)"""

#: an MCP tool name as Claude Code reports it: mcp__<server>__<tool>
MCP_TOOL = re.compile(r"^mcp__(.+?)__(emir_[A-Za-z0-9_]+)$")


def cards(selected=None) -> list:
    out = []
    for n in sorted(os.listdir(CARDS_DIR)):
        if n.endswith(".yaml"):
            c = grade.load_card(os.path.join(CARDS_DIR, n))
            if not selected or c["id"] in selected:
                out.append(c)
    if selected:
        unknown = set(selected) - {c["id"] for c in out}
        if unknown:
            raise SystemExit(f"run.py: unknown card(s) {sorted(unknown)}; --list shows them")
    return out


def missing_requirements(card) -> list:
    import importlib.util
    miss = []
    for req in card.get("requires", []):
        if req in ("klayout", "matplotlib", "mcp", "gdstk"):
            if importlib.util.find_spec(req) is None:
                miss.append(req)
        elif shutil.which({"xyce": "Xyce"}.get(req, req)) is None:
            miss.append(req)
    return miss


def real_emir() -> list:
    """The emir the shim forwards to: the one beside this Python, else on PATH,
    else `python -m emir` (from a directory without an emir/ package)."""
    cand = os.path.join(os.path.dirname(sys.executable), "emir")
    if os.path.isfile(cand) and os.access(cand, os.X_OK):
        return [cand]
    w = shutil.which("emir")
    return [w] if w else [sys.executable, "-m", "emir"]


def _tracked() -> list:
    try:
        p = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", "--cached", "--others",
                            "--exclude-standard"], capture_output=True,
                           text=True, check=True)
        return [f for f in p.stdout.split("\0") if f]
    except (OSError, subprocess.CalledProcessError):
        out = []
        for d, dirs, names in os.walk(ROOT):
            dirs[:] = [x for x in dirs if x not in (".git", ".venv", "venv", "build", "dist",
                                                    "emir_out", "out", "__pycache__")
                       and not x.endswith(".egg-info")]
            out += [os.path.relpath(os.path.join(d, n), ROOT) for n in names]
        return out


def make_workspace(card, ws: str, emir: list) -> dict:
    """The repository copy, the fixtures, the shim; returns the meta document."""
    if os.path.exists(ws):
        shutil.rmtree(ws)
    for rel in _tracked():
        if rel.startswith("agent/evals/") or rel.startswith("agent" + os.sep + "evals"):
            continue
        src, dst = os.path.join(ROOT, rel), os.path.join(ws, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.islink(src):
            os.symlink(os.readlink(src), dst)
        elif os.path.isfile(src):
            shutil.copy2(src, dst)
    fixtures.build(card.get("fixtures", []), os.path.join(ws, "inputs"), emir=emir)
    ev = os.path.join(ws, ".eval")
    os.makedirs(os.path.join(ev, "bin"), exist_ok=True)
    shim = os.path.join(ev, "bin", "emir")
    with open(os.path.join(HERE, "shim.py"), encoding="utf-8") as f:
        body = f.read()
    with open(shim, "w", encoding="utf-8") as f:
        f.write(f"#!{sys.executable}\n")
        f.write(f"REAL = {json.dumps(emir)}\nLOG = {json.dumps(os.path.join(ev, 'calls.jsonl'))}\n")
        f.write(body)
    os.chmod(shim, 0o755)
    copy_bin = os.path.join(ws, "bin", "emir")
    if os.path.lexists(copy_bin):
        os.remove(copy_bin)
        os.symlink(shim, copy_bin)
    digest = grade.digest_tree(ws, ["inputs", *card.get("expect", {}).get("unchanged", [])])
    meta = {"card": card["id"], "inputs_digest": digest, "emir": emir,
            "allowed_limits": grade.allowed_limits(ws, card.get("expect", {})
                                                   .get("limits_from", []))}
    return meta


def mcp_argv(tool: str, args: dict) -> list | None:
    """The emir command line (after `emir`) of one MCP tool call, or None."""
    from emir import mcp_tools
    spec = mcp_tools.specs().get(tool)
    if spec is None:
        return None
    args = {k: v for k, v in (args or {}).items() if k != mcp_tools.CONFIRM}
    if spec.name == mcp_tools.GENERIC:
        import emircmd
        line = str(args.get("command_line", "")).strip()
        line = line if line.startswith("emir ") else "emir " + line
        try:
            return emircmd.emir_argv(emircmd.split(line))
        except ValueError:
            return None
    try:
        return [spec.command, *mcp_tools.to_argv(spec, args)]
    except Exception:                         # noqa: BLE001 - graded as not a real call
        return [spec.command, *(f"--{k}={v}" for k, v in args.items())]


def _tool_rc(item: dict) -> int | None:
    """The exit status a tool result stands for: an emir error document's
    `exit_code`, else 0 (an error with no document: 2)."""
    content = item.get("content")
    texts = [c.get("text", "") for c in content if isinstance(c, dict)] \
        if isinstance(content, list) else [content if isinstance(content, str) else ""]
    for t in texts:
        try:
            doc = json.loads(t)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("schema") == "emir.error/1":
            return doc.get("exit_code", 2)
        if isinstance(doc, dict) and "schema" in doc:
            return 0
    return 2 if item.get("is_error") else 0


def parse_agent_output(stdout: str) -> dict:
    """Cost, time, turns, the final text, the shell commands and the emir MCP
    tool calls, from `claude -p` JSON or stream-json output; `{}` for anything
    else. A tool call the harness refused (the result's `permission_denials`)
    is left out: it never ran."""
    out, cmds, mcp = {}, [], []
    lines = [ln for ln in stdout.splitlines() if ln.strip().startswith("{")]
    docs = []
    for ln in lines:
        try:
            docs.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    if not docs:
        try:
            docs = [json.loads(stdout)]
        except (json.JSONDecodeError, ValueError):
            return {}
    denied = {x.get("tool_use_id") for d in docs if isinstance(d, dict)
              and d.get("type") == "result" for x in (d.get("permission_denials") or [])
              if isinstance(x, dict)}
    results = {}
    for d in docs:
        if isinstance(d, dict) and d.get("type") == "user":
            for item in (d.get("message") or {}).get("content") or []:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    results[item.get("tool_use_id")] = item
    for d in docs:
        if not isinstance(d, dict):
            continue
        if d.get("type") == "assistant":
            for item in (d.get("message") or {}).get("content") or []:
                if not isinstance(item, dict) or item.get("type") != "tool_use" or \
                        item.get("id") in denied:
                    continue
                if item.get("name") == "Bash":
                    c = (item.get("input") or {}).get("command")
                    if c:
                        cmds.append(c)
                    continue
                m = MCP_TOOL.match(item.get("name") or "")
                if m:
                    argv = mcp_argv(m.group(2), item.get("input") or {})
                    if argv:
                        res = results.get(item.get("id"))
                        mcp.append({"argv": argv, "tool": m.group(2),
                                    "rc": _tool_rc(res) if res else None})
        if d.get("type") == "result":
            out.update(cost_usd=d.get("total_cost_usd"), turns=d.get("num_turns"),
                       agent_ms=d.get("duration_ms"), final=d.get("result"),
                       is_error=d.get("is_error"), denied=len(denied))
    if cmds:
        out["commands"] = cmds
    if mcp:
        out["mcp_calls"] = mcp
    return out


def mcp_config(ws: str, emir: list) -> str:
    """An MCP client file (Claude Code's `--mcp-config` form) that starts the
    real `emir mcp` in the workspace; returns its path."""
    p = os.path.join(ws, ".eval", "mcp.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {"emir": {"type": "stdio", "command": emir[0],
                                           "args": [*emir[1:], "mcp"]}}}, f, indent=1)
    return p


def run_card(card, agent_cmd: str, out_dir: str, timeout: float, prompt_stdin: bool,
             mcp: bool = False) -> dict:
    ws = os.path.join(out_dir, card["id"], "ws")
    os.makedirs(os.path.dirname(ws), exist_ok=True)
    miss = missing_requirements(card)
    if miss:
        return {"card": card["id"], "playbook": card.get("playbook"), "skipped": True,
                "passed": None, "reason": f"needs {', '.join(miss)}"}
    emir = real_emir()
    meta = make_workspace(card, ws, emir)
    ev = os.path.join(ws, ".eval")
    env = dict(os.environ)
    env["PATH"] = os.path.join(ev, "bin") + os.pathsep + env.get("PATH", "")
    env.pop("EMIR_EVAL_CARD", None)
    if "fake_agent.py" in agent_cmd:
        # the scripted agent replays the card's reference; a real agent must
        # never be able to read the card (it holds the answers)
        env["EMIR_EVAL_CARD"] = card["_path"]
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    prompt = card["prompt"].rstrip() + (SUFFIX_MCP if mcp else SUFFIX)
    if "{mcp_config}" in agent_cmd:
        agent_cmd = agent_cmd.replace("{mcp_config}", shlex.quote(mcp_config(ws, emir)))
    argv = shlex.split(agent_cmd)
    # a relative program or script path is taken from where run.py was started
    argv = [os.path.abspath(t) if ("/" in t and not os.path.isabs(t) and os.path.exists(t))
            else t for t in argv]
    argv += [] if prompt_stdin else [prompt]
    meta["started"] = time.time()
    with open(os.path.join(ev, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    t0 = time.time()
    try:
        p = subprocess.run(argv, cwd=ws, env=env, input=prompt if prompt_stdin else None,
                           capture_output=True, text=True, timeout=timeout)
        rc, stdout, stderr = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        rc = "timeout"
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
    seconds = time.time() - t0
    for name, text in (("agent_stdout.txt", stdout), ("agent_stderr.txt", stderr)):
        with open(os.path.join(ev, name), "w", encoding="utf-8") as f:
            f.write(text)
    info = parse_agent_output(stdout)
    if info.get("commands"):
        with open(os.path.join(ev, "transcript_commands.json"), "w", encoding="utf-8") as f:
            json.dump(info["commands"], f, indent=1)
    if info.get("mcp_calls"):
        with open(os.path.join(ev, "mcp_calls.json"), "w", encoding="utf-8") as f:
            json.dump(info["mcp_calls"], f, indent=1)
    final = info.get("final") if info else None
    with open(os.path.join(ev, "answer.txt"), "w", encoding="utf-8") as f:
        f.write(final if isinstance(final, str) else stdout)
    res = grade.grade(card, ws, since=meta["started"], inputs_digest=meta["inputs_digest"])
    res.update(seconds=round(seconds, 1), agent_exit=rc, cost_usd=info.get("cost_usd"),
               turns=info.get("turns"), emir_calls=len(grade.emir_calls(ws)),
               permission_denials=info.get("denied"), workspace=ws)
    with open(os.path.join(out_dir, card["id"], "result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    return res


def table(results) -> str:
    rows = ["| card | playbook | result | score | s | cost USD | turns | failed checks |",
            "|---|---|---|---|---|---|---|---|"]
    for r in results:
        if r.get("skipped"):
            rows.append(f"| {r['card']} | {r.get('playbook')} | skipped ({r['reason']}) "
                        f"| | | | | |")
            continue
        cost = "" if r.get("cost_usd") is None else f"{r['cost_usd']:.3f}"
        rows.append(f"| {r['card']} | {r['playbook']} | {'PASS' if r['passed'] else 'FAIL'} "
                    f"| {r['score']:.0%} | {r['seconds']} | {cost} | {r.get('turns') or ''} "
                    f"| {', '.join(r['failed'])} |")
    return "\n".join(rows)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--agent-cmd", help="the agent CLI; the prompt is appended as its last "
                                        "argument (e.g. 'claude -p', 'codex exec')")
    ap.add_argument("--cards", default=None, help="comma-separated card ids (default: all)")
    ap.add_argument("--out", default=None, help="output directory (default: "
                                                "emir_out/evals/<time>)")
    ap.add_argument("--timeout", type=float, default=900.0, help="seconds per card")
    ap.add_argument("--jobs", type=int, default=1,
                    help="cards run at once (each may simulate: keep to the person's "
                         "simulation-concurrency limit)")
    ap.add_argument("--prompt-stdin", action="store_true",
                    help="send the prompt on stdin instead of as the last argument (needed "
                         "when the agent command ends with an option that takes several "
                         "values, as Claude Code's --allowedTools does)")
    ap.add_argument("--mcp", action="store_true",
                    help="the agent drives emir over MCP: `{mcp_config}` in --agent-cmd "
                         "becomes an MCP client file that starts `emir mcp`, the prompt says "
                         "to use the emir_* tools, and their calls are graded")
    ap.add_argument("--list", action="store_true", help="list the cards and exit")
    a = ap.parse_args(argv)
    sel = [c.strip() for c in a.cards.split(",")] if a.cards else None
    cs = cards(sel)
    if a.list:
        for c in cs:
            miss = missing_requirements(c)
            print(f"{c['id']:24s} {c['playbook']:28s} {c['title']}"
                  + (f"  [needs {', '.join(miss)}]" if miss else ""))
        return 0
    if not a.agent_cmd:
        ap.error("--agent-cmd is required (or --list)")
    out = os.path.abspath(a.out or os.path.join(ROOT, "emir_out", "evals",
                                                time.strftime("%Y%m%d-%H%M%S")))
    os.makedirs(out, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        results = list(ex.map(lambda c: run_card(c, a.agent_cmd, out, a.timeout,
                                                 a.prompt_stdin, a.mcp), cs))
    summary = {"agent_cmd": a.agent_cmd, "out": out, "results": results,
               "passed": sum(1 for r in results if r.get("passed")),
               "graded": sum(1 for r in results if not r.get("skipped")),
               "cost_usd": round(sum(r.get("cost_usd") or 0 for r in results), 4)}
    with open(os.path.join(out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    print(table(results))
    print(f"\n{summary['passed']}/{summary['graded']} passed; results: "
          f"{os.path.join(out, 'results.json')}")
    return 0 if summary["passed"] == summary["graded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
