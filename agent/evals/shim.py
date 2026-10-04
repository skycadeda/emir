# The logging shim the eval runner installs as `emir` (first on PATH) in a workspace.
# run.py writes the interpreter line and REAL (the real emir command) and LOG (the call
# log) above this text. It runs the real emir with the same arguments, stdin, stdout and
# stderr, then appends {argv, cwd, rc, seconds} to LOG and exits with emir's status.
import json  # noqa: E402
import os  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

# an agent harness ends a long-lived emir (`emir mcp`) with SIGTERM: exit through
# `finally`, so the call is still logged
signal.signal(signal.SIGTERM, lambda *_a: sys.exit(143))
t0 = time.time()
rc = 130
try:
    rc = subprocess.run(REAL + sys.argv[1:]).returncode  # noqa: F821 - written by run.py
except KeyboardInterrupt:
    rc = 130
except SystemExit as e:
    rc = e.code if isinstance(e.code, int) else 143
finally:
    rec = {"argv": sys.argv[1:], "cwd": os.getcwd(), "rc": rc,
           "seconds": round(time.time() - t0, 3)}
    with open(LOG, "a", encoding="utf-8") as f:  # noqa: F821 - written by run.py
        f.write(json.dumps(rec) + "\n")
sys.exit(rc)
