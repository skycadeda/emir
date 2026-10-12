# Connecting an agent to EMIR

EMIR is built to be driven by an AI agent: every command answers in JSON (`--format json`), every failure carries
a code, every result says what to run next, and `emir next` turns what the agent has into an ordered plan. The
agent's entry point is [`AGENTS.md`](../AGENTS.md) at the repository root; it points at the
[playbooks](playbooks/). This page is for the person connecting an agent. **Screening, not sign-off.**

## Any agent with a shell

Point it at this repository (or install EMIR and give it `AGENTS.md`). The first three commands it should run:

```sh
emir --version
emir info --format json
emir selftest --format json
```

Then `emir next` with the person's files, and the plan it prints. Agents that read `AGENTS.md` by themselves
(Codex, and others that follow the AGENTS.md convention) need nothing else when started in a clone.

## Claude Code

- **In a clone:** `CLAUDE.md` imports `AGENTS.md`, and the skill in [`skills/emir/`](skills/emir/SKILL.md) is found
  through `.claude/skills/emir` (a relative symlink to it), so starting `claude` in the clone is enough.
- **In another project:** make the skill a personal one, so it loads wherever EM/IR comes up:

  ```sh
  mkdir -p ~/.claude/skills && ln -s "$PWD/agent/skills/emir" ~/.claude/skills/emir
  ```

  (from the clone's root). The skill tells Claude to read this clone's `AGENTS.md` and playbooks.
- **As typed tools (MCP):** `claude mcp add emir -- emir mcp` in the project directory; see [`mcp/`](mcp/README.md).

## Codex

Codex reads `AGENTS.md` at the root of the repository it works in. For another project, add a line to that
project's `AGENTS.md`: "EM/IR screening: follow `<path to the emir clone>/AGENTS.md`". As MCP tools: the
[`mcp/codex-config.toml`](mcp/codex-config.toml) snippet goes into `~/.codex/config.toml`.

## Cursor

Recent Cursor versions read `AGENTS.md` at the project root; with an older one, add a project rule
(`.cursor/rules/emir.mdc`) that says to read `AGENTS.md` before running emir. As MCP tools: the
[`mcp/cursor-mcp.json`](mcp/cursor-mcp.json) snippet goes into `.cursor/mcp.json`.

## Any MCP client

Transport stdio, command `emir`, arguments `["mcp"]` ([`mcp/generic.json`](mcp/generic.json)). `emir mcp --list`
prints every client's snippet with this machine's absolute path to `emir`. Tool calls that would start a licensed
tool, run long or rewrite a file are refused (EMIR-E590) until the agent repeats them with `confirm: true`, which it
may do only after the person agreed. Details: [`mcp/README.md`](mcp/README.md) and playbook
[00](playbooks/00-preflight.md).

## What is in `agent/`

| path | what |
|---|---|
| [`playbooks/`](playbooks/) | one playbook per task, 00 to 04, each with a worked example that runs as printed |
| [`skills/emir/`](skills/emir/SKILL.md) | the Claude Code skill |
| [`mcp/`](mcp/README.md) | MCP client snippets (the server is `emir mcp`) |
| [`cadence/`](cadence/README.md) | the Cadence integration: Virtuoso SKILL (and its headless self-test), PVS/Quantus, Spectre templates |
| [`opensource/`](opensource/README.md) | Xyce, ngspice, KLayout scripts |
| [`CONTRACT.md`](CONTRACT.md) | the agent contract, generated from the code: every command and its options, the documents, exit statuses, codes, next steps, MCP tools |
| [`schemas/`](schemas/) | the JSON Schema of every document (`emir schema <name>` prints one) |
| [`errors.md`](errors.md) | every code with its meaning, hint and command (`emir explain <code>`) |
| [`evals/`](evals/README.md) | task cards, graders and a runner that score an agent on these playbooks |

## Checking an agent

`agent/evals/run.py --agent-cmd '<your agent CLI>'` gives the agent realistic requests on the examples' files, in a
scratch copy of this repository, and grades what it did and said: the right commands (each parsed with EMIR's own
parser, so an invented option fails), the right verdict and worst ratio, the governing metric named, the
disclaimer present, no invented limit, no input file changed. See [`evals/README.md`](evals/README.md).

## What has been verified

| client | how | status |
|---|---|---|
| Claude Code (CLI, `claude -p`) reading `CLAUDE.md` -> `AGENTS.md` in a clone | `agent/evals/run.py`, one run per card, on 2026-10-02 and 2026-10-04 (`first-spectre-run`, `ict-encrypted` and `period-refused`, the 9th to 11th of the cards, have not run with a real agent) | 6 of the 8 cards run pass with the current grader; the two misses were the agent's, and the docs now steer against both. Not re-run since. The runs, their cost and time: [`evals/README.md`](evals/README.md) |
| Claude Code with the MCP server (`--mcp-config`, no shell) | `screen-dc`, graded from the transcript's tool calls (`run.py --mcp`) | 2026-10-02 (Claude Code 2.1.287): PASS |
| the skill through `.claude/skills/emir` | a test checks the relative link resolves | the link: tested; its triggering: not measured |
| Codex, Cursor, Claude Desktop | not installed where this repository is built | UNVERIFIED: the snippets follow each client's published format |
