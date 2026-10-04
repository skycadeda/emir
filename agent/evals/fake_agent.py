"""A scripted stand-in for an agent CLI, to test the graders and the runner.

    python agent/evals/run.py --agent-cmd 'python agent/evals/fake_agent.py --variant good'

It reads the card the runner is grading (`$EMIR_EVAL_CARD`), runs the card's
`reference.commands` in the workspace one by one (through the shim, so they are
logged like a real agent's), and writes `reference.answer` to answer.md. The
prompt (its last argument) is ignored. Variants make a known mistake, so a test
can check that the grader catches it:

  good            the reference, as written
  no-disclaimer   the answer without "Screening, not sign-off."
  invented-flag   an extra emir call with an option emir does not have
  wrong-number    every number in the answer scaled by 1.5
  invented-limit  the answer quotes a limit no process file holds
  signoff         the answer calls the result sign-off
  edit-input      appends a line to the first input file
  no-commands     writes the answer without running anything
  wrong-verdict   the answer's verdict words swapped (FAIL <-> CLEAN)
  pick-mode       chooses the mode for the person: an `emir save-lines --mode rcc` call
                  on the first DSPF of inputs/, and "I chose rcc" in the answer
  loosen-basis    every emir command with --dynamic run without its --period (decap-aware
                  EM is then not computed and the verdict falls back to decap-free)
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

VARIANTS = ("good", "no-disclaimer", "invented-flag", "wrong-number", "invented-limit",
            "signoff", "edit-input", "no-commands", "wrong-verdict", "pick-mode",
            "loosen-basis")


def _scale(m):
    s = m.group(0)
    v = float(s) * 1.5
    return f"{v:.4g}"


def answer_for(text: str, variant: str) -> str:
    if variant == "no-disclaimer":
        text = re.sub(r"\**screening[\s,]*not sign[\s-]?off\.?\**", "", text, flags=re.I)
    elif variant == "wrong-number":
        text = re.sub(r"(?<![\w.])\d+\.\d+|(?<![\w.])\d+(?![\w.])", _scale, text)
    elif variant == "invented-limit":
        text += "\nThe Metal1 limit here is 9.87 mA/um.\n"
    elif variant == "signoff":
        text += "\nThis block is sign-off clean.\n"
    elif variant == "wrong-verdict":
        text = re.sub(r"\bFAIL(S|ED)?\b", "CLEAN", text)
        text = re.sub(r"\bfail(s|ed|ing)?\b", "pass", text)
    elif variant == "pick-mode":
        text += "\nI chose rcc for you: it is exact on a net this small.\n"
    return text


def main(argv=None) -> int:
    import argparse

    import grade
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--variant", default="good", choices=VARIANTS)
    ap.add_argument("prompt", nargs="?")
    a = ap.parse_args(argv)
    card = grade.load_card(os.environ["EMIR_EVAL_CARD"])
    ref = card.get("reference") or {}
    cmds = list(ref.get("commands", []))
    if a.variant == "no-commands":
        cmds = []
    if a.variant == "invented-flag":
        cmds.insert(1, "emir check inputs --fast --format json")
    if a.variant == "pick-mode":
        dspf = next((os.path.join(d, n) for d, _, names in sorted(os.walk("inputs"))
                     for n in sorted(names) if n.endswith(".dspf")), None)
        if dspf:
            cmds.append(f"emir save-lines {dspf} --mode rcc --format json")
    if a.variant == "loosen-basis":
        cmds = [re.sub(r" --period \S+", "", c) if " --dynamic" in c else c for c in cmds]
    if a.variant == "edit-input":
        for d, _, names in sorted(os.walk("inputs")):
            if names:
                with open(os.path.join(d, sorted(names)[0]), "a", encoding="utf-8") as f:
                    f.write("\n* edited\n")
                break
    for c in cmds:
        p = subprocess.run(["bash", "-o", "pipefail", "-c", c], capture_output=True, text=True)
        sys.stderr.write(f"$ {c}\n[exit {p.returncode}]\n{p.stderr[-800:]}")
    text = answer_for(ref.get("answer", ""), a.variant)
    with open("answer.md", "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
