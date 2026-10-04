"""Deterministic grader of one eval task card.

    python agent/evals/grade.py --card agent/evals/cards/screen-dc.yaml --ws WORKSPACE [--json]

It reads what the agent left in the workspace -- the emir calls the logging shim
recorded (`.eval/calls.jsonl`), the emir tool calls an agent made over MCP
(`.eval/mcp_calls.json`, which the runner rebuilds from the transcript: those
calls run inside `emir mcp`, where the shim sees nothing), the shell commands
of its transcript when the runner could parse one
(`.eval/transcript_commands.json`; used only when neither of the others saw a
call), the documents emir wrote, the input files, and the answer (`answer.md`,
else `.eval/answer.txt`) -- and checks them against the card's `expect`. No model judges anything: every check
is a parse, a comparison or a regular expression, so the same workspace always
gets the same grade.

The checks (each card may switch the defaults off, or add its own):

* `answer`: the agent wrote an answer;
* `commands`: the card's command patterns appear, in order, among the emir calls
  (`commands_all`: each appears, in any order; `commands_any`: at least one does);
* `forbid`: no emir call matches a forbidden pattern (`extract --run` ...);
* `real_options`: every emir command the agent ran parses with emir's own parser
  (an invented option or a misspelt command fails). Only calls that ran count: a
  template command the agent wrote into its answer (`--period <T>` in a heredoc)
  or a command the agent's harness refused is not a call;
* `preflight`: `emir selftest` ran before the first command that reads the inputs;
* `documents`: emir documents the agent produced hold the expected fields;
* `files`: files the agent was asked to write exist and match their patterns
  (`match`), match none of `none`, and, with `values: none`, a process file
  holds no value typed from a document (every limit, resistance, temperature,
  derating and source null, but a stacking source `emir process new` inferred):
  a fill-in file left for the person to fill;
* `verdict`, `numbers`, `metric`, `all`, `any`, `none`: what the answer says;
* `disclaimer`: "Screening, not sign-off." (or the same words) is in the answer;
* `no_signoff_claim`: the answer never calls the result sign-off;
* `no_invented_limits`: every limit the answer quotes in mA/um or mA/cut (a value
  with "limit" within 40 characters of it) is in the process file(s) the card names
  (or a limit emir derived from them); a current density the answer works out is not
  a limit;
* `unchanged`: the input files are byte for byte what the agent was given.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import emircmd  # noqa: E402

#: commands that read the person's inputs (preflight must come before them)
READS_INPUTS = {"check", "run", "abstract", "simulate", "probe", "extract",
                "save-lines", "nets", "pins", "next", "report", "import-ict", "process"}

DISCLAIMER = re.compile(r"screening[\s,;:.\-–—]*(?:and\s+|but\s+)?not\s+(?:a\s+)?sign[\s-]?off",
                        re.I)
SIGNOFF_CLAIM = re.compile(
    r"\b(?:is|are|as|counts?\s+as|serves?\s+as|ready\s+for|qualifies\s+as)\s+(?:a\s+|the\s+)?"
    r"sign[\s-]?off\b(?!\s*(?:flow|tool))|\bsigned[\s-]off\b|\bsign[\s-]?off[\s-](?:clean|ready|"
    r"passed|quality)\b", re.I)
VERDICT_WORDS = {"FAIL": r"\bfail", "WARN": r"\bwarn", "CLEAN": r"\bclean\b", "PASS": r"\bpass"}
METRIC_WORDS = {"avg": r"\bavg\b|\baverage\b", "rms": r"\brms\b", "peak": r"\bpeak\b"}
NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(\s*%)?")
LIMIT = re.compile(r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*(mA|A|uA|µA)\s*(?:/|per\s+)\s*"
                   r"(um|µm|μm|micron|cut)", re.I)


#: the keys of a process file whose values come from the PDK's documents
VALUE_KEYS = {"em_tref", "temp_reference", "ea_eV", "n", "jmax_factor", "sheet_res", "tc1",
              "tc2", "width_loss_um", "jmax_avg", "jmax_rms", "jmax_rms_k", "jmax_peak",
              "min_width", "gds", "res", "vcwidth", "imax_avg", "imax_rms", "imax_peak",
              "cj", "cjsw", "cox", "ron_sq", "unit_confirmed"}


def filled_values(data) -> list:
    """The paths of the values a process file gives (`VALUE_KEYS` not null)
    and of the sources it names (but the `inferred ...` stacking sources
    `emir process new` writes)."""
    out = []

    def walk(x, path):
        if not isinstance(x, dict):
            return
        for k, v in x.items():
            p = f"{path}/{k}" if path else str(k)
            if k == "sources" and isinstance(v, dict):
                out.extend(f"{p}/{sk}" for sk, sv in v.items() if sv is not None and not (
                    isinstance(sv, str) and sv.strip().lower().startswith("inferred")))
                continue
            if k in VALUE_KEYS and v not in (None, {}, []):
                out.append(p)
            elif isinstance(v, dict):
                walk(v, p)
    walk(data, "")
    return out


# ------------------------------------------------------------------ inputs


def load_card(path: str) -> dict:
    import yaml
    with open(path, encoding="utf-8") as f:
        card = yaml.safe_load(f)
    card.setdefault("expect", {})
    card["_path"] = os.path.abspath(path)
    return card


def read_calls(ws: str) -> list:
    """The emir calls the shim logged: `[{argv, cwd, rc, seconds}]`, in order."""
    p = os.path.join(ws, ".eval", "calls.jsonl")
    out = []
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return out


_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def strip_heredocs(cmd: str) -> str:
    """A shell command without its here-document bodies: `cat > answer.md
    <<'EOF'` ... `EOF` is text the agent wrote, not commands it ran."""
    out, end = [], None
    for line in cmd.split("\n"):
        if end is not None:
            if line.strip() == end:
                end = None
            continue
        out.append(line)
        m = _HEREDOC.search(line)
        if m:
            end = m.group(2)
    return "\n".join(out)


def read_transcript_commands(ws: str) -> list:
    """The shell commands of the agent's transcript (the runner left out the
    ones its harness refused), without here-document bodies."""
    p = os.path.join(ws, ".eval", "transcript_commands.json")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return [strip_heredocs(c) for c in json.load(f) if isinstance(c, str)]
    return []


def read_mcp_calls(ws: str) -> list:
    """The emir tool calls the agent made over MCP: `[{argv, rc}]` (the runner
    turns each `mcp__<server>__emir_<command>` tool call back into its command
    line with `emir.mcp_tools.to_argv`)."""
    p = os.path.join(ws, ".eval", "mcp_calls.json")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return []


def read_answer(ws: str) -> str:
    for rel in ("answer.md", os.path.join(".eval", "answer.txt")):
        p = os.path.join(ws, rel)
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read()
            if text.strip():
                return text
    return ""


def digest_tree(root: str, rels) -> dict:
    """`{relpath: sha256}` of every file under each of `rels` (files or directories)."""
    out = {}
    for rel in rels:
        top = os.path.join(root, rel)
        paths = [top] if os.path.isfile(top) else [
            os.path.join(d, n) for d, _, names in os.walk(top) for n in names]
        for p in paths:
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = hashlib.sha256(f.read()).hexdigest()
    return out


def documents(ws: str, since: float) -> list:
    """Every emir JSON document written in the workspace after `since` (outside
    `.eval/`): `[(relpath, doc)]`, newest first."""
    found = []
    for d, dirs, names in os.walk(ws):
        dirs[:] = [x for x in dirs if x not in (".eval", ".git", "__pycache__")]
        for n in names:
            if not n.endswith(".json"):
                continue
            p = os.path.join(d, n)
            try:
                if os.path.getmtime(p) < since:
                    continue
                with open(p, encoding="utf-8") as f:
                    doc = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(doc, dict) and str(doc.get("schema", "")).startswith("emir."):
                found.append((os.path.getmtime(p), os.path.relpath(p, ws), doc))
    found.sort(key=lambda x: -x[0])
    return [(rel, doc) for _, rel, doc in found]


# ------------------------------------------------------------------ matching


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def opt_values(argv: list, opt: str) -> list:
    """The values given to `opt` in an argv (`--opt v` and `--opt=v`)."""
    out = []
    for i, t in enumerate(argv):
        if t == opt and i + 1 < len(argv):
            out.append(argv[i + 1])
        elif t.startswith(opt + "="):
            out.append(t.split("=", 1)[1])
    return out


def has_opt(argv: list, opt: str) -> bool:
    return any(t == opt or t.startswith(opt + "=") for t in argv)


def matches(call_argv: list, pat: dict) -> bool:
    """Does one emir call (`argv`, the command first) match a card pattern?

    `cmd` the command (with its sub-command: `process new`); `has` options
    present; `lacks` options absent; `value` {option: value or [values]}
    (numbers compare as numbers, paths by their end); `text` substrings of
    the joined argv; `rc` its exit status."""
    words = str(pat["cmd"]).split() if pat.get("cmd") else []
    if not call_argv or (words and call_argv[:len(words)] != words):
        return False
    argv = call_argv[len(words) or 1:]
    for o in pat.get("has", []):
        if not has_opt(argv, o):
            return False
    for o in pat.get("lacks", []):
        if has_opt(argv, o):
            return False
    for o, want in (pat.get("value") or {}).items():
        wants = want if isinstance(want, list) else [want]
        got = opt_values(argv, o)
        ok = False
        for g in got:
            for w in wants:
                gn, wn = _num(g), _num(w)
                if gn is not None and wn is not None:
                    ok = ok or math.isclose(gn, wn, rel_tol=1e-9, abs_tol=0.0)
                else:
                    ok = ok or g == str(w) or os.path.normpath(g).endswith(
                        os.path.normpath(str(w)))
        if not ok:
            return False
    joined = " ".join(argv)
    for t in pat.get("text", []):
        if t not in joined:
            return False
    return True


def get_path(doc, path: str):
    cur = doc
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return KeyError
        elif isinstance(cur, dict):
            if part not in cur:
                return KeyError
            cur = cur[part]
        else:
            return KeyError
    return cur


def field_ok(value, want) -> bool:
    if value is KeyError:
        return False
    if isinstance(want, dict):
        if "approx" in want:
            return (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isclose(value, want["approx"], rel_tol=want.get("rel", 1e-3),
                                     abs_tol=want.get("abs", 0.0)))
        if "endswith" in want:
            return isinstance(value, str) and value.endswith(want["endswith"])
        if "contains" in want:
            return want["contains"] in (value if isinstance(value, str) else json.dumps(value))
        if "len" in want:
            return isinstance(value, (list, dict)) and len(value) == want["len"]
    return value == want


def numbers_in(text: str) -> list:
    out = []
    for m in NUMBER.finditer(text):
        v = _num(m.group(0).replace("%", "").strip())
        if v is None:
            continue
        out.append(v / 100.0 if m.group(1) else v)
        out.append(v)
    return out


def allowed_limits(ws: str, files: list) -> list:
    """Every number of the process files (and the rms limits emir derives from a
    `jmax_rms_k` at the default 5 C), in mA/um or mA/cut."""
    import yaml
    vals = []

    def walk(x, key=None):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, k)
            if "jmax_rms_k" in x and isinstance(x["jmax_rms_k"], (int, float)):
                vals.append(math.sqrt(x["jmax_rms_k"] * 5.0))
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
        elif isinstance(x, (int, float)) and not isinstance(x, bool):
            vals.append(float(x))
    for rel in files:
        p = os.path.join(ws, rel)
        try:
            with open(p, encoding="utf-8") as f:
                walk(yaml.safe_load(f))
        except (OSError, yaml.YAMLError):
            pass                    # changed or removed: the `unchanged` check says so
    return vals


# ------------------------------------------------------------------ grading


class Result:
    def __init__(self):
        self.checks = []

    def add(self, name, ok, detail=""):
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})

    @property
    def passed(self):
        return all(c["ok"] for c in self.checks)

    def as_dict(self, card):
        n = len(self.checks)
        return {"card": card["id"], "playbook": card.get("playbook"), "passed": self.passed,
                "score": round(sum(c["ok"] for c in self.checks) / n, 3) if n else 0.0,
                "failed": [c["name"] for c in self.checks if not c["ok"]],
                "checks": self.checks}


def emir_calls(ws: str) -> list:
    """`[{argv, rc, source}]`: the shim's log and the MCP tool calls; the
    transcript's emir commands when neither saw a call (an agent that called
    emir another way)."""
    calls = [{"argv": c.get("argv", []), "rc": c.get("rc"), "source": "shim"}
             for c in read_calls(ws)]
    calls += [{"argv": c.get("argv", []), "rc": c.get("rc"), "source": "mcp"}
              for c in read_mcp_calls(ws)]
    if not calls:
        for line in read_transcript_commands(ws):
            for part in re.split(r"&&|\|\||;|\n", line):
                try:
                    argv = emircmd.emir_argv(emircmd.split(part.strip()))
                except ValueError:
                    argv = None
                if argv:
                    calls.append({"argv": argv, "rc": None, "source": "transcript"})
    return calls


def grade(card: dict, ws: str, since: float | None = None, inputs_digest: dict | None = None
          ) -> dict:
    """Grade one workspace against one card; returns the result document."""
    exp = card.get("expect", {})
    r = Result()
    answer = read_answer(ws)
    calls = emir_calls(ws)
    meta = {}
    mp = os.path.join(ws, ".eval", "meta.json")
    if os.path.isfile(mp):
        with open(mp, encoding="utf-8") as f:
            meta = json.load(f)
    if since is None:
        since = meta.get("started", 0.0)
    if inputs_digest is None:
        inputs_digest = meta.get("inputs_digest")

    r.add("answer", bool(answer.strip()), "" if answer.strip() else "no answer.md, no final text")

    # --- the commands: the ones that ran (the shim's log, the MCP calls; the
    # transcript only when neither saw any, without heredoc bodies or refused calls)
    bad = []
    for c in calls:
        err = emircmd.check_argv(c["argv"])
        if err:
            bad.append(f"emir {' '.join(c['argv'])}: {err}")
    r.add("real_options", not bad, "; ".join(sorted(set(bad)))[:600])

    seq = exp.get("commands", [])
    if seq:
        i, missing = 0, None
        for pat in seq:
            while i < len(calls) and not (matches(calls[i]["argv"], pat) and
                                          (pat.get("rc") is None or calls[i]["rc"] == pat["rc"])):
                i += 1
            if i == len(calls):
                missing = pat
                break
            i += 1
        r.add("commands", missing is None,
              "" if missing is None else f"no emir call matching {missing} (in this order)")
    every = exp.get("commands_all", [])
    if every:
        lost = [p for p in every if not any(matches(c["argv"], p) and
                                            (p.get("rc") is None or c["rc"] == p["rc"])
                                            for c in calls)]
        r.add("commands_all", not lost, "" if not lost else f"no emir call matching {lost}")
    alts = exp.get("commands_any", [])
    if alts:
        hit = [p for p in alts if any(matches(c["argv"], p) for c in calls)]
        r.add("commands_any", bool(hit), "" if hit else f"no emir call matching any of {alts}")
    for pat in exp.get("forbid", []):
        hit = [c for c in calls if matches(c["argv"], pat)]
        r.add(f"forbid:{pat.get('cmd', '*')}", not hit,
              "" if not hit else f"ran emir {' '.join(hit[0]['argv'])}")
    if exp.get("preflight", True):
        first_read = next((k for k, c in enumerate(calls) if c["argv"] and
                           c["argv"][0] in READS_INPUTS), None)
        st = next((k for k, c in enumerate(calls) if c["argv"][:1] == ["selftest"]), None)
        ok = first_read is None or (st is not None and st < first_read)
        r.add("preflight", ok, "" if ok else "no `emir selftest` before the first command "
                                             "on the person's files")

    # --- the documents
    docs = documents(ws, since)
    for spec in exp.get("documents", []):
        cands = [(rel, d) for rel, d in docs if d.get("schema") == spec["schema"]]
        best = None
        for rel, d in cands:
            bad_f = [k for k, w in (spec.get("fields") or {}).items()
                     if not field_ok(get_path(d, k), w)]
            if not bad_f:
                best = (rel, None)
                break
            if best is None:
                best = (rel, bad_f)
        ok = best is not None and best[1] is None
        detail = ("no document with schema " + spec["schema"]) if best is None else (
            "" if ok else f"{best[0]}: " + ", ".join(
                f"{k}={get_path(dict(cands)[best[0]], k)!r} (want {spec['fields'][k]!r})"
                for k in best[1]))
        r.add(f"document:{spec['schema']}", ok, detail)

    for spec in exp.get("files", []):
        p = os.path.join(ws, spec["path"])
        text = ""
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read()
        bad_rx = [rx for rx in spec.get("match", []) if not re.search(rx, text, re.M)]
        bad_rx += [f"none: {rx}" for rx in spec.get("none", []) if re.search(rx, text, re.M)]
        if spec.get("values") == "none" and os.path.isfile(p):
            import yaml
            try:
                filled = filled_values(yaml.safe_load(text))
            except yaml.YAMLError as e:
                filled = [f"not YAML: {e}"]
            if filled:
                bad_rx.append("values given (none may be: the person types them): "
                              + ", ".join(filled[:8]))
        ok = os.path.isfile(p) and not bad_rx
        r.add(f"file:{spec['path']}", ok, "" if ok else (
            "not written" if not os.path.isfile(p) else f"does not match {bad_rx}"))

    # --- the answer
    a = exp.get("answer", {})
    if "verdict" in a:
        ok = re.search(VERDICT_WORDS[a["verdict"]], answer, re.I) is not None
        r.add("verdict", ok, "" if ok else f"the answer does not give the verdict {a['verdict']}")
    nums = numbers_in(answer)
    for spec in a.get("numbers", []):
        v, rel = spec["value"], spec.get("rel", 0.01)
        ok = any(math.isclose(x, v, rel_tol=rel) for x in nums)
        r.add(f"number:{spec.get('what', v)}", ok,
              "" if ok else f"no number within {rel:.1%} of {v} in the answer")
    if "metric" in a:
        ok = re.search(METRIC_WORDS[a["metric"]], answer, re.I) is not None
        r.add("metric", ok, "" if ok else f"the governing metric ({a['metric']}) is not named")
    for rx in a.get("all", []):
        ok = re.search(rx, answer, re.I | re.S) is not None
        r.add(f"says:{rx}", ok, "" if ok else f"the answer does not match /{rx}/")
    for group in a.get("any", []):
        ok = any(re.search(rx, answer, re.I | re.S) for rx in group)
        r.add(f"says-one-of:{'|'.join(group)}", ok,
              "" if ok else f"the answer matches none of {group}")
    for rx in a.get("none", []):
        m = re.search(rx, answer, re.I | re.S)
        r.add(f"never-says:{rx}", m is None, "" if m is None else f"the answer says {m.group(0)!r}")
    if exp.get("disclaimer", True):
        ok = DISCLAIMER.search(answer) is not None
        r.add("disclaimer", ok, "" if ok else "no 'Screening, not sign-off.' in the answer")
    m = SIGNOFF_CLAIM.search(answer)
    r.add("no_signoff_claim", m is None, "" if m is None else f"the answer says {m.group(0)!r}")
    if exp.get("limits_from"):
        # read before the agent ran (run.py), so an edited file cannot widen them
        allowed = meta.get("allowed_limits") or allowed_limits(ws, exp["limits_from"])
        invented = []
        for m in LIMIT.finditer(answer):
            near = answer[max(0, m.start() - 40):m.end() + 40]
            if not re.search(r"\blimit", near, re.I):
                continue                    # a density the agent worked out, not a limit
            v = float(m.group(1))
            unit = m.group(2).lower()
            v = v * 1e3 if unit == "a" else v / 1e3 if unit in ("ua", "µa") else v
            if not any(math.isclose(v, x, rel_tol=2e-3) for x in allowed):
                invented.append(m.group(0))
        r.add("no_invented_limits", not invented,
              "" if not invented else f"limits not in {exp['limits_from']}: {invented}")

    # --- the inputs
    if inputs_digest is not None:
        now = digest_tree(ws, sorted({k.split(os.sep)[0] for k in inputs_digest}
                                     | set(exp.get("unchanged", []))))
        changed = sorted(k for k, v in inputs_digest.items() if now.get(k) != v)
        r.add("unchanged", not changed, "" if not changed else f"changed or removed: {changed}")
    return r.as_dict(card)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--card", required=True)
    ap.add_argument("--ws", required=True, help="the workspace the agent worked in")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args(argv)
    res = grade(load_card(a.card), a.ws)
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print(f"{res['card']}: {'PASS' if res['passed'] else 'FAIL'} ({res['score']:.0%})")
        for c in res["checks"]:
            print(f"  {'ok  ' if c['ok'] else 'FAIL'} {c['name']}" +
                  (f": {c['detail']}" if c["detail"] else ""))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
