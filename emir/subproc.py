"""Running a simulator so that stopping it stops all of it.

A site's `Xyce`, `ngspice`, `spectre` or `hspice` is often a wrapper (a module
script, an `mpirun` launcher) that starts the simulator as its child.
`subprocess.run(timeout=)` kills only the process it started, so on a time
limit the wrapper died and the simulator itself ran on: past the run that
reported it stopped, against the one-simulation-at-a-time rule, writing a
result into a directory the next pass reuses. `run` starts the command in a
session of its own and, on a time limit or an interrupt, stops the whole
process group: SIGTERM, a grace period, then SIGKILL for anything left.
"""
from __future__ import annotations

import os
import signal
import subprocess

#: seconds the group gets between SIGTERM and SIGKILL
GRACE_S = 5.0


def _signal_group(p: subprocess.Popen, sig) -> None:
    try:
        os.killpg(p.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def stop(p: subprocess.Popen, grace: float = GRACE_S) -> None:
    """Stop `p` and every process it started in its group."""
    if os.name != "posix":
        p.kill()
        return
    _signal_group(p, signal.SIGTERM)
    try:
        p.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    # whatever is left of the group (a child that outlived its wrapper, or
    # one that ignores SIGTERM)
    _signal_group(p, signal.SIGKILL)


def run(cmd, timeout: float | None = None, cwd=None, grace: float = GRACE_S,
        **kw) -> subprocess.CompletedProcess:
    """`subprocess.run(cmd, capture_output=True, text=True, stdin=DEVNULL,
    timeout=timeout, cwd=cwd)`, except that a time limit or an interrupt
    stops the command's whole process group before raising
    (`subprocess.TimeoutExpired` with what it printed, or the interrupt)."""
    kw.setdefault("stdin", subprocess.DEVNULL)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         cwd=cwd, start_new_session=os.name == "posix", **kw)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop(p, grace)
        try:
            out, err = p.communicate(timeout=grace)
        except subprocess.TimeoutExpired:      # a process that left the group holds a pipe
            out, err = "", ""
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err) from None
    except BaseException:
        stop(p, grace)
        p.wait()
        raise
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)
