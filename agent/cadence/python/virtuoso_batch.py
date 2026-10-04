"""Run a SKILL body in batch Virtuoso and say whether it worked.

Status: UNVERIFIED until checked on site. The pattern -- `virtuoso -nograph
-restore <script> -log <log>`, the body wrapped in errset, OK/FAIL markers, the
log read back when stdout is lost -- is tested here with a stand-in executable
only. On the machine that has Virtuoso, the local agent verifies it (after
asking the person: batch Virtuoso takes a licence) with the headless SKILL
self-test this module carries:

    python agent/cadence/python/virtuoso_batch.py --selftest [--skill-dir D]

which loads EMIR's SKILL (`emir_main.il`) in batch Virtuoso and checks that its
procedures are defined; `agent/cadence/LIVE_TEST.md` then checks the
interactive side.

This is how Python drives Virtuoso in EMIR's world: one-shot batch jobs, for a
maintainer or an agent that needs a CIW answer (does a library exist, which
functions does this release have). The interactive direction is the other way
round: the EMIR form starts `emir` with ipcBeginProcess and streams its output,
and results come back as generated SKILL (`emir_data.il`), never over a socket.
Never use it to start, stop or restart a person's own Virtuoso session.

    from virtuoso_batch import run_skill
    out = run_skill('printf("CELL|%s\\n" ddGetObj("MYLIB" "myblock")~>name)', "vrun")

The cds.lib to chain comes from the argument, `$EMIR_CDS_LIB`, or the site
file key `cds_lib` (when the emir package is installed); none is assumed.
"""
from __future__ import annotations

import os
import re
import subprocess

OK_MARKER = "EMIR_OK"
FAIL_MARKER = "EMIR_FAIL"
#: the prefix the `-log` transcript puts on each line: a backslash and a
#: letter (output, input, warning, error, prompt, return value ...) or `#`.
#: Which letters a release uses is UNVERIFIED; any lower-case letter is taken.
LOG_PREFIX = re.compile(r"^\\[a-z#]\s?")


class SkillError(RuntimeError):
    """The SKILL body raised an error, or the job never reported."""


def wrap(body: str) -> str:
    """The script: `body` inside errset, a marker either way, then exit. An
    error that is not caught leaves batch Virtuoso at an interactive prompt
    (seen during development)."""
    return ("(if (errset (progn\n" + body + "\n) t)"
            f' (printf "{OK_MARKER}\\n")\n'
            f'  (printf "{FAIL_MARKER} %L\\n" errset.errset))\n(exit)\n')


def strip_log(lines) -> str:
    """The transcript of a `-log` file without its line prefixes."""
    return "".join(LOG_PREFIX.sub("", ln) for ln in lines)


def make_run_dir(run_dir: str, cds_lib: str | None = None) -> str:
    """`run_dir`, with a cds.lib that INCLUDEs the given one (if any)."""
    os.makedirs(run_dir, exist_ok=True)
    cds_lib = cds_lib or os.environ.get("EMIR_CDS_LIB") or None
    if cds_lib is None:
        try:
            from emir import site               # the emir package, when installed
            cds_lib = site.get("cds_lib")[0] or None
        except ImportError:
            cds_lib = None
    target = os.path.join(run_dir, "cds.lib")
    if cds_lib and not os.path.exists(target):
        with open(target, "w", encoding="utf-8") as f:
            f.write(f"INCLUDE {os.path.abspath(cds_lib)}\n")
    return run_dir


def run_skill(body: str, run_dir: str, tag: str = "job", timeout: float = 600,
              cds_lib: str | None = None, virtuoso: str = "virtuoso") -> str:
    """Run `body`; return the transcript, or raise `SkillError`.

    A batch launch took from about 25 s to about 3 minutes of wall time during
    development, most of it after the SKILL had finished: give `timeout` room,
    and batch several questions into one body.
    """
    run_dir = os.path.abspath(make_run_dir(run_dir, cds_lib))
    script = os.path.join(run_dir, f"{tag}.il")
    log = os.path.join(run_dir, f"{tag}.virtuoso.log")
    with open(script, "w", encoding="utf-8") as f:
        f.write(wrap(body))
    p = subprocess.run([virtuoso, "-nograph", "-restore", script, "-log", log],
                       cwd=run_dir, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=timeout)
    out = p.stdout + p.stderr
    if OK_MARKER not in out and FAIL_MARKER not in out:
        # a stalled exit can lose stdout while the log holds the transcript
        try:
            with open(log, encoding="utf-8", errors="replace") as f:
                out += strip_log(f)
        except OSError:
            pass
    if FAIL_MARKER in out or OK_MARKER not in out:
        detail = "\n".join(ln for ln in out.splitlines()
                           if FAIL_MARKER in ln or "ERROR" in ln.upper())[:2000]
        raise SkillError(f"SKILL job {tag!r} failed (see {log}):\n{detail}")
    return out


# ------------------------------------------------------------------ the self-test

#: procedures emir_main.il defines that the menu and the form call
SELFTEST_PROCS = ("emirSettings", "emirCmdline", "emirMain", "emirSaveLinesForm",
                  "emirLoadViewer", "emirAddMenu")
SKILL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "skill")


def selftest_body(skill_dir: str = SKILL_DIR) -> str:
    """The SKILL the headless self-test runs: load emir_main.il, print one
    `EMIR_SELFTEST|<procedure>|<callable>` line per procedure, and fail
    (inside errset) when one is missing."""
    main = os.path.join(os.path.abspath(skill_dir), "emir_main.il")
    if re.search(r'["\\\n]', main):
        raise SkillError(f"{main}: a path with a quote, a backslash or a line break")
    procs = " ".join(f"'{p}" for p in SELFTEST_PROCS)
    return (f'load("{main}")\n'
            f'foreach(f list({procs})\n'
            f'  printf("EMIR_SELFTEST|%L|%L\\n" f isCallable(f)))\n'
            f'unless(forall(f list({procs}) isCallable(f))\n'
            f'  error("EMIR: a procedure of emir_main.il is not defined"))\n'
            f'emirSettings()\n')


def selftest(skill_dir: str = SKILL_DIR, run_dir: str = os.path.join(
        "emir_out", "virtuoso_selftest"), virtuoso: str = "virtuoso",
        timeout: float = 600) -> dict:
    """Run the headless self-test; `{"passed", "procedures", "transcript"}`
    (a failure is `passed` false, never an exception)."""
    try:
        out = run_skill(selftest_body(skill_dir), run_dir, tag="emir_selftest",
                        timeout=timeout, virtuoso=virtuoso)
    except (SkillError, OSError, subprocess.SubprocessError) as e:
        return {"passed": False, "procedures": {}, "transcript": str(e)}
    found = dict(re.findall(r"EMIR_SELFTEST\|(\w+)\|(\S+)", out))
    ok = all(found.get(p) == "t" for p in SELFTEST_PROCS)
    return {"passed": ok, "procedures": found, "transcript": out[-4000:]}


def main(argv=None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Batch Virtuoso helpers for EMIR.")
    ap.add_argument("--selftest", action="store_true",
                    help="load EMIR's SKILL in batch Virtuoso and check its procedures")
    ap.add_argument("--skill-dir", default=SKILL_DIR,
                    help="the directory holding emir_main.il (default: this clone's)")
    ap.add_argument("--run-dir", default=os.path.join("emir_out", "virtuoso_selftest"))
    ap.add_argument("--virtuoso", default=os.environ.get("VIRTUOSO_BIN", "virtuoso"))
    ap.add_argument("--timeout", type=float, default=600)
    a = ap.parse_args(argv)
    if not a.selftest:
        ap.print_help()
        return 2
    r = selftest(a.skill_dir, a.run_dir, a.virtuoso, a.timeout)
    print(json.dumps({k: r[k] for k in ("passed", "procedures")}, indent=1))
    print("headless SKILL self-test:", "PASS" if r["passed"] else "FAIL")
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
