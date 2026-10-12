"""The agent evals (agent/evals/): the cards, the graders, the runner.

The graders are tested with a scripted fake agent (agent/evals/fake_agent.py)
driven through the real runner: each card's reference transcript must pass
every check (so the cards' expected numbers are the ones emir really
produces), and each known mistake must be caught by the check meant for it.
No real agent runs here.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
EVALS = os.path.join(ROOT, "agent", "evals")
for p in (ROOT, EVALS):
    if p not in sys.path:
        sys.path.insert(0, p)

import emircmd  # noqa: E402
import fixtures  # noqa: E402
import grade  # noqa: E402
import run as runner  # noqa: E402

CARDS = runner.cards()
BY_ID = {c["id"]: c for c in CARDS}
PLAYBOOKS = sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, "agent", "playbooks")))
FAKE = os.path.join(EVALS, "fake_agent.py")


def fake(variant="good"):
    return f"{sys.executable} {FAKE} --variant {variant}"


def run_card(card_id, tmp_path, variant="good"):
    card = BY_ID[card_id]
    miss = runner.missing_requirements(card)
    if miss:
        pytest.skip(f"{card_id} needs {miss}")
    return runner.run_card(card, fake(variant), str(tmp_path), timeout=300, prompt_stdin=False)


# ------------------------------------------------------------------ the cards


def test_at_least_ten_cards_cover_every_playbook():
    assert len(CARDS) >= 10
    assert {c["playbook"] for c in CARDS} == set(PLAYBOOKS)
    assert len(BY_ID) == len(CARDS), "card ids must be unique"


@pytest.mark.parametrize("card", CARDS, ids=lambda c: c["id"])
def test_cards_are_well_formed(card):
    from emir import cli
    for k in ("id", "playbook", "title", "prompt", "expect", "reference"):
        assert card.get(k), (card["id"], k)
    assert os.path.basename(card["_path"]).endswith(f"{card['id']}.yaml")
    assert set(card.get("fixtures", [])) <= set(fixtures.FIXTURES)
    assert set(card.get("requires", [])) <= {"xyce", "ngspice", "magic", "klayout",
                                              "matplotlib", "mcp"}
    exp = card["expect"]
    pats = (exp.get("commands", []) + exp.get("commands_all", []) + exp.get("commands_any", [])
            + exp.get("forbid", []))
    for pat in pats:
        words = pat["cmd"].split()           # a sub-command: `process new`
        assert words[0] in cli.COMMANDS, (card["id"], pat)
        assert len(words) == 1 or words[1] in cli.SUBCOMMANDS.get(words[0], ()), pat
        argv = [*words, *pat.get("has", []), *pat.get("lacks", []),
                *(pat.get("value") or {})]
        assert not emircmd.unknown_options(argv), (card["id"], pat)
    assert exp.get("commands") or exp.get("commands_all") or exp.get("commands_any"), card["id"]
    # the reference is a real transcript: every emir command parses
    for line in card["reference"]["commands"]:
        argv, err = emircmd.check_line(line)
        assert err is None, (card["id"], line, err)
    # the prompt does not give the answer away
    for spec in exp.get("answer", {}).get("numbers", []):
        assert f"{spec['value']}" not in card["prompt"], (card["id"], spec)
    assert "sign-off" not in card["prompt"].lower()
    # what the reference answer says, the checks accept
    if exp.get("disclaimer", True):
        assert grade.DISCLAIMER.search(card["reference"]["answer"]), card["id"]


def test_the_agent_never_sees_the_evals(tmp_path):
    card = BY_ID["mcp-connect"]
    ws = str(tmp_path / "ws")
    runner.make_workspace(card, ws, runner.real_emir())
    assert not os.path.exists(os.path.join(ws, "agent", "evals"))
    assert os.path.isfile(os.path.join(ws, "AGENTS.md"))
    assert os.path.isfile(os.path.join(ws, "agent", "playbooks", "01-screen-a-block.md"))
    assert os.access(os.path.join(ws, ".eval", "bin", "emir"), os.X_OK)


# ------------------------------------------------------------------ the reference passes


@pytest.mark.parametrize("card_id", sorted(BY_ID))
def test_the_reference_transcript_passes_every_check(card_id, tmp_path):
    res = run_card(card_id, tmp_path)
    assert res["passed"], [c for c in res["checks"] if not c["ok"]]
    calls = grade.read_calls(res["workspace"])
    # every call was logged with its exit status
    assert calls and all(isinstance(c["rc"], int) for c in calls), calls
    assert res["emir_calls"] == len(calls)


# ------------------------------------------------------------------ the mistakes are caught


MISTAKES = [("no-disclaimer", "screen-dc", {"disclaimer"}),
            ("invented-flag", "screen-dc", {"real_options"}),
            ("invented-flag", "cadence-extract-plan", {"real_options"}),
            ("wrong-number", "screen-dc", {"number:worst ratio", "number:IR drop mV"}),
            ("invented-limit", "report-for-manager", {"no_invented_limits"}),
            ("signoff", "screen-dc", {"no_signoff_claim"}),
            ("edit-input", "screen-dc", {"unchanged"}),
            ("edit-input", "onboard-gf180", {"unchanged"}),
            ("no-commands", "screen-dc", {"commands", "document:emir.run/1"}),
            ("no-commands", "cadence-extract-plan", {"commands", "file:emir_out/emir_save.scs"}),
            ("wrong-verdict", "report-for-manager", {"verdict"}),
            ("invented-limit", "ict-encrypted", {"no_invented_limits"}),
            ("no-commands", "ict-encrypted", {"commands", "file:emir_out/mypdk.yaml"})]


@pytest.mark.parametrize("variant,card_id,expected", MISTAKES,
                         ids=[f"{v}-{c}" for v, c, _ in MISTAKES])
def test_each_mistake_is_caught(variant, card_id, expected, tmp_path):
    res = run_card(card_id, tmp_path, variant)
    assert not res["passed"]
    assert expected <= set(res["failed"]), res["failed"]


# ------------------------------------------------------------------ pieces


def test_a_forbidden_call_and_a_late_selftest_fail(tmp_path):
    card = BY_ID["cadence-extract-plan"]
    ws = tmp_path / "ws"
    (ws / ".eval").mkdir(parents=True)
    calls = [["extract", "--gds", "inputs/railblock.gds", "--cdl", "inputs/railblock.cdl",
              "--cell", "railblock", "--kind", "r_only", "--run"], ["selftest"]]
    with open(ws / ".eval" / "calls.jsonl", "w") as f:
        for a in calls:
            f.write(json.dumps({"argv": a, "rc": 3}) + "\n")
    (ws / "answer.md").write_text("I ran it.\n")
    res = grade.grade(card, str(ws), since=0.0, inputs_digest={})
    assert {"forbid:extract", "preflight", "commands"} <= set(res["failed"])


def test_transcript_commands_are_graded_when_the_shim_saw_nothing(tmp_path):
    """An agent that called emir another way (python -m emir) is still parsed."""
    stream = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "python -m emir selftest --format json"}},
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "cd x && python3 -m emir run a.dspf --process p.yaml --fast"}}]}},
        {"type": "result", "total_cost_usd": 0.0123, "num_turns": 3, "duration_ms": 4567,
         "result": "FAIL. Screening, not sign-off.", "is_error": False}]
    info = runner.parse_agent_output("\n".join(json.dumps(x) for x in stream))
    assert info["cost_usd"] == 0.0123 and info["turns"] == 3
    assert info["final"].startswith("FAIL") and len(info["commands"]) == 2
    ws = tmp_path / "ws"
    (ws / ".eval").mkdir(parents=True)
    (ws / ".eval" / "transcript_commands.json").write_text(json.dumps(info["commands"]))
    (ws / ".eval" / "answer.txt").write_text(info["final"])
    res = grade.grade({"id": "t", "expect": {"commands": [{"cmd": "selftest"}]}}, str(ws),
                      since=0.0, inputs_digest={})
    assert "real_options" in res["failed"] and "--fast" in res["checks"][1]["detail"]
    assert "commands" not in res["failed"]


def test_answer_checks_read_numbers_percentages_and_limits(tmp_path):
    assert any(abs(x - 1.667) < 1e-9 for x in grade.numbers_in("worst 1.667x, 166.7 %"))
    assert any(abs(x - 1.667) < 1e-9 for x in grade.numbers_in("166.7%"))
    assert grade.DISCLAIMER.search("This is screening, not sign-off.")
    assert grade.DISCLAIMER.search("**Screening, not sign-off.**")
    assert not grade.SIGNOFF_CLAIM.search("Screening, not sign-off. The sign-off flow remains "
                                          "the foundry's.")
    assert grade.SIGNOFF_CLAIM.search("The block is sign-off clean.")
    assert grade.SIGNOFF_CLAIM.search("this counts as sign-off")
    m = [x.group(0) for x in grade.LIMIT.finditer("3.2 mA/um and 0.75 mA per cut, 2e-3 A/um")]
    assert m == ["3.2 mA/um", "0.75 mA per cut", "2e-3 A/um"]


def test_grade_and_run_command_lines(tmp_path):
    p = subprocess.run([sys.executable, os.path.join(EVALS, "run.py"), "--list"],
                       capture_output=True, text=True)
    assert p.returncode == 0 and all(c in p.stdout for c in BY_ID)
    res = run_card("mcp-connect", tmp_path)
    p = subprocess.run([sys.executable, os.path.join(EVALS, "grade.py"), "--card",
                        BY_ID["mcp-connect"]["_path"], "--ws", res["workspace"], "--json"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads(p.stdout)["passed"] is True


# ------------------------------------------------------------------ what counts as a call


def _stream(*items, denied=()):
    docs = [{"type": "system", "subtype": "init"}]
    for it in items:
        docs.append(it)
    docs.append({"type": "result", "total_cost_usd": 0.01, "num_turns": 2, "duration_ms": 10,
                 "result": "done. Screening, not sign-off.", "is_error": False,
                 "permission_denials": [{"tool_name": "Bash", "tool_use_id": d,
                                         "tool_input": {}} for d in denied]})
    return "\n".join(json.dumps(d) for d in docs)


def _bash(tid, command):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command}}]}}


def test_template_commands_in_a_heredoc_or_a_refused_call_are_not_calls(tmp_path):
    """A correct answer that shows the person template commands (`--period <T>`)
    through `cat > answer.md <<'EOF'` failed real_options, and so did a call
    the agent's harness refused: neither ran."""
    heredoc = ("cat > answer.md <<'EOF'\nRun this when you have the period:\n"
               "emir check inputs/x.dspf --process p.yaml --period <T>\nEOF")
    out = _stream(_bash("t1", "emir selftest --format json"),
                  _bash("t2", heredoc),
                  _bash("t3", "emir run x.dspf --fast"), denied=["t3"])
    info = runner.parse_agent_output(out)
    assert info["denied"] == 1 and len(info["commands"]) == 2
    assert all("--fast" not in c for c in info["commands"])
    ws = tmp_path / "ws"
    (ws / ".eval").mkdir(parents=True)
    (ws / ".eval" / "transcript_commands.json").write_text(json.dumps(info["commands"]))
    (ws / "answer.md").write_text("Screening, not sign-off.\n")
    # graded from the transcript (the shim saw nothing): the heredoc body is text
    res = grade.grade({"id": "t", "expect": {"commands": [{"cmd": "selftest"}]}}, str(ws),
                      since=0.0, inputs_digest={})
    assert res["passed"], res["failed"]
    # with a shim log, the shim's calls are what ran: the transcript is not re-read
    with open(ws / ".eval" / "calls.jsonl", "w") as f:
        f.write(json.dumps({"argv": ["selftest", "--format", "json"], "rc": 0}) + "\n")
    (ws / ".eval" / "transcript_commands.json").write_text(json.dumps(
        ["emir run x.dspf --made-up-in-a-comment"]))
    res = grade.grade({"id": "t", "expect": {}}, str(ws), since=0.0, inputs_digest={})
    assert "real_options" not in res["failed"]


def test_mcp_tool_calls_are_graded_as_emir_calls(tmp_path):
    """Over MCP the calls run inside `emir mcp`: the shim saw nothing and every
    MCP card failed `commands` and `preflight`. The runner now rebuilds each
    tool call's command line from the transcript."""
    from emir.selftest import SAMPLE_CURRENTS, sample_paths
    dspf, proc = sample_paths()
    run_args = {"dspf": dspf, "process": proc, "currents": SAMPLE_CURRENTS,
                "summary_out": "emir_out/summary.json"}
    out = _stream(
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "m1", "name": "mcp__emir__emir_selftest", "input": {}},
            {"type": "tool_use", "id": "m2", "name": "mcp__emir__emir_run", "input": run_args},
            {"type": "tool_use", "id": "m3", "name": "mcp__emir__emir_cli",
             "input": {"command_line": "emir report --from emir_out/summary.json"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "m1",
             "content": [{"type": "text", "text": json.dumps({"schema": "emir.selftest/1"})}]},
            {"type": "tool_result", "tool_use_id": "m2", "is_error": True,
             "content": [{"type": "text", "text": json.dumps(
                 {"schema": "emir.error/1", "code": "EMIR-E590", "exit_code": 2})}]}]}})
    info = runner.parse_agent_output(out)
    calls = info["mcp_calls"]
    assert [c["argv"][0] for c in calls] == ["selftest", "run", "report"]
    assert calls[0]["rc"] == 0 and calls[1]["rc"] == 2 and calls[2]["rc"] is None
    assert "--currents" in calls[1]["argv"] and "--summary-out" in calls[1]["argv"]
    ws = tmp_path / "ws"
    (ws / ".eval").mkdir(parents=True)
    (ws / ".eval" / "mcp_calls.json").write_text(json.dumps(calls))
    (ws / "answer.md").write_text("FAIL. Screening, not sign-off.\n")
    card = {"id": "t", "expect": {"commands": [{"cmd": "selftest"},
                                               {"cmd": "run", "has": ["--currents"]},
                                               {"cmd": "report"}]}}
    res = grade.grade(card, str(ws), since=0.0, inputs_digest={})
    assert res["passed"], res["checks"]


@pytest.mark.parametrize("order", [["info", "selftest"], ["selftest", "info"]])
def test_the_preflight_card_takes_either_documented_order(tmp_path, order):
    """AGENTS.md, playbook 00 and the skill run `info` then `selftest`; the card
    required the opposite order, so an agent following AGENTS.md failed."""
    ws = tmp_path / "ws"
    (ws / ".eval").mkdir(parents=True)
    with open(ws / ".eval" / "calls.jsonl", "w") as f:
        for c in order:
            f.write(json.dumps({"argv": [c, "--format", "json"], "rc": 0}) + "\n")
    card = BY_ID["preflight-install"]
    res = grade.grade(card, str(ws), since=0.0, inputs_digest={})
    assert "commands_all" not in res["failed"] and "commands" not in res["failed"]


def test_a_density_the_agent_works_out_is_not_a_limit(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".eval").mkdir(parents=True)
    (ws / ".eval" / "meta.json").write_text(json.dumps({"allowed_limits": [0.67]}))
    card = {"id": "t", "expect": {"limits_from": ["x.yaml"], "disclaimer": False}}
    (ws / "answer.md").write_text("It carries 0.25 mA / 0.5 um = 0.5 mA/um against 0.67, "
                                  "about 0.75x.\n")
    assert "no_invented_limits" not in grade.grade(card, str(ws), 0.0, {})["failed"]
    (ws / "answer.md").write_text("The Metal1 limit here is 9.87 mA/um.\n")
    assert "no_invented_limits" in grade.grade(card, str(ws), 0.0, {})["failed"]


def test_a_real_agent_never_sees_the_card(tmp_path):
    """$EMIR_EVAL_CARD (the card, with its reference answer) is for the
    scripted agent only."""
    card = BY_ID["mcp-connect"]
    cmd = f"{sys.executable} -c \"import os; open('seen.txt', 'w').write(" \
          f"os.environ.get('EMIR_EVAL_CARD', 'none'))\""
    res = runner.run_card(card, cmd, str(tmp_path), timeout=120, prompt_stdin=True)
    assert open(os.path.join(res["workspace"], "seen.txt")).read() == "none"


def test_the_runner_writes_an_mcp_client_file(tmp_path):
    card = BY_ID["mcp-connect"]
    cmd = f"{sys.executable} -c \"import sys; open('cfg.txt', 'w').write(sys.argv[1])\" " \
          "{mcp_config}"
    res = runner.run_card(card, cmd, str(tmp_path), timeout=120, prompt_stdin=True, mcp=True)
    cfg = open(os.path.join(res["workspace"], "cfg.txt")).read()
    doc = json.load(open(cfg))
    assert doc["mcpServers"]["emir"]["args"][-1] == "mcp"


def test_the_readmes_name_every_card_not_run_with_a_real_agent():
    """The results table of agent/evals/README.md lists the cards run with a
    real agent; the "came later" sentences of agent/README.md and
    agent/evals/README.md must name every other card."""
    import glob
    import re

    import yaml
    root = os.path.join(os.path.dirname(__file__), os.pardir)
    ids = []
    for p in sorted(glob.glob(os.path.join(root, "agent", "evals", "cards", "*.yaml"))):
        with open(p, encoding="utf-8") as f:
            ids.append(yaml.safe_load(f)["id"])
    with open(os.path.join(root, "agent", "evals", "README.md"), encoding="utf-8") as f:
        ev = f.read()
    run = set(re.findall(r"^\| `([a-z0-9-]+)` \| (?:PASS|FAIL)", ev, re.M))
    later = [i for i in ids if i not in run]
    assert run and later and len(run) + len(later) == len(ids)
    with open(os.path.join(root, "agent", "README.md"), encoding="utf-8") as f:
        ag = f.read()
    n0 = len(run) + 1
    for where, text in (("agent/README.md", ag), ("agent/evals/README.md", ev)):
        flat = " ".join(text.split())
        assert f"the {n0}th to {len(ids)}th" in flat, where
        m = re.search(rf"\(([^()]*?)the {n0}th to {len(ids)}th[^()]*\)", flat)
        assert m, where
        named = set(re.findall(r"`([a-z0-9-]+)`", m.group(0)))
        assert named == set(later), (where, named ^ set(later))
