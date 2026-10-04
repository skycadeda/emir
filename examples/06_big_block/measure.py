"""Wall time and peak memory of one command, measured in a clean child.

`run(cmd, log)` starts a small launcher process, which starts `cmd` and
waits for it with `os.wait4`; the launcher reports the command's wall time
and its peak resident set size (`ru_maxrss`). The launcher exists because a
child forked straight from a process that already holds a lot of memory
starts with that process's high-water mark, so its own peak could not be
told from the parent's.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys

#: one thread for the numerical libraries, so a measurement does not depend
#: on how many cores happen to be free (the simulators run serial too)
SERIAL = {k: "1" for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                           "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")}

_LAUNCHER = r"""
import json, os, subprocess, sys, time
cmd, log, cwd = json.loads(sys.argv[1]), sys.argv[2], sys.argv[3] or None
load = os.getloadavg()[0]
t0 = time.time()
with open(log, "w", encoding="utf-8") as f:
    p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=cwd)
    _, status, ru = os.wait4(p.pid, 0)
wall = time.time() - t0
kb = 1024 if sys.platform == "darwin" else 1
print(json.dumps({"wall_s": wall, "peak_rss_MB": ru.ru_maxrss / kb / 1024,
                  "exit": os.waitstatus_to_exitcode(status), "load_1min": load}))
"""


def run(cmd: list, log: str, cwd: str | None = None, repeat: int = 1) -> dict:
    """`{"wall_s", "peak_rss_MB", "exit", "load_1min", "command"}` for `cmd`,
    run in `cwd` with single-threaded numerical libraries (stdout and stderr
    go to `log`). With `repeat` > 1 it runs that many times and keeps the
    fastest (the least disturbed by other work on the machine); `load_1min`
    is the machine's load average when the kept run started."""
    best = None
    for _ in range(max(1, repeat)):
        p = subprocess.run([sys.executable, "-c", _LAUNCHER,
                            json.dumps([str(c) for c in cmd]), os.path.abspath(log),
                            cwd or ""], capture_output=True, text=True, check=True,
                           env={**os.environ, **SERIAL})
        out = json.loads(p.stdout)
        if best is None or out["wall_s"] < best["wall_s"]:
            best = out
    best["command"] = shlex.join(str(c) for c in cmd)
    return best
