"""Stand-ins for the Cadence executables, for tests only.

They are NOT Cadence tools and model nothing of them but what EMIR's
orchestration touches: the command line, the files in the run directory and
the exit status. Every call appends its argv (as JSON) to `$FAKE_LOG`.

* `pvs` writes `<cell>.lvsrpt.cls` in its run directory, saying MATCH (or
  MISMATCH with `FAKE_LVS=mismatch`, or nothing with `FAKE_LVS=none`), and an
  `svdb/` directory.
* `qrc -cmd FILE` reads the command file, and writes the `-file_name` DSPF in
  its run directory: the bundled sample, with the command file's
  `-temperature` as `*|OPERATING_TEMPERATURE` (nothing with `FAKE_QRC=none`;
  it sleeps first with `FAKE_SLEEP=S`).
* `virtuoso -nograph -restore SCRIPT -log LOG` prints EMIR_OK (or EMIR_FAIL
  when the script contains "FAIL_ME"); with `FAKE_VIRTUOSO=log` it prints
  nothing and writes the marker into the log, with a `\\o` prefix. For the
  headless self-test it answers each `EMIR_SELFTEST` procedure as callable
  (`t`), except the one `FAKE_MISSING` names.
"""
import json
import os
import stat
import sys

from _helpers import DSPF

_COMMON = r'''#!{python}
import json, os, re, sys, time
log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({{"tool": {name!r}, "argv": sys.argv[1:], "cwd": os.getcwd()}}) + "\n")
'''

_PVS = _COMMON + r'''
a = sys.argv[1:]
cell = a[a.index("-layout_top_cell") + 1]
mode = os.environ.get("FAKE_LVS", "match")
os.makedirs("svdb", exist_ok=True)
if mode != "none":
    with open(f"{{cell}}.lvsrpt.cls", "w") as f:
        f.write("stand-in LVS report\n Run Result : %s\n" % ("MATCH" if mode == "match" else "MISMATCH"))
print("stand-in pvs done")
'''

_QRC = _COMMON + r'''
a = sys.argv[1:]
ccl = open(a[a.index("-cmd") + 1]).read()
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
if os.environ.get("FAKE_QRC", "write") == "none":
    sys.exit(1)
name = re.findall(r'-file_name "([^"]+)"', ccl)[-1]   # output_setup's, not log_file's
temp = re.search(r"-temperature ([-\d.]+)", ccl).group(1)
text = open({sample!r}).read()
text = re.sub(r"\*\|OPERATING_TEMPERATURE [-\d.]+", "*|OPERATING_TEMPERATURE " + temp, text)
with open(name, "w") as f:
    f.write(text)
print("stand-in qrc wrote", name)
'''

_VIRTUOSO = _COMMON + r'''
a = sys.argv[1:]
script = open(a[a.index("-restore") + 1]).read()
logf = a[a.index("-log") + 1]
marker = "EMIR_FAIL (\"boom\")" if "FAIL_ME" in script else "EMIR_OK"
if "EMIR_SELFTEST" in script:
    # the headless self-test: each procedure it asks about, callable unless
    # FAKE_MISSING names it
    missing = os.environ.get("FAKE_MISSING", "")
    procs = re.findall(r"'(emir\w+)", script.split("forall")[0])
    marker = "\n".join("EMIR_SELFTEST|%s|%s" % (p, "nil" if p == missing else "t")
                       for p in procs) + "\n" + ("EMIR_FAIL (\"missing\")" if missing
                                                 else "EMIR_OK")
if os.environ.get("FAKE_VIRTUOSO") == "log":
    with open(logf, "w") as f:
        f.write("\\i load script\n\\o " + marker + "\n")
else:
    print(marker)
'''


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def make_tools(d) -> str:
    """Write `pvs`, `qrc` and `virtuoso` stand-ins into directory `d`."""
    d = str(d)
    os.makedirs(d, exist_ok=True)
    py = sys.executable
    _write(os.path.join(d, "pvs"), _PVS.format(python=py, name="pvs"))
    _write(os.path.join(d, "qrc"), _QRC.format(python=py, name="qrc", sample=DSPF))
    _write(os.path.join(d, "virtuoso"), _VIRTUOSO.format(python=py, name="virtuoso"))
    return d


def make_decks(d) -> dict:
    """A rule deck (with a pvs_control_file beside it) and a Quantus
    technology directory with a corner `typ`: empty stand-ins."""
    d = str(d)
    os.makedirs(os.path.join(d, "pdk", "qrc", "typ"), exist_ok=True)
    rules = os.path.join(d, "pdk", "lvs.rul")
    with open(rules, "w") as f:
        f.write("// stand-in rule deck\n")
    with open(os.path.join(d, "pdk", "pvs_control_file"), "w") as f:
        f.write("// stand-in control file\n")
    return {"rules": rules, "tech_dir": os.path.join(d, "pdk", "qrc"), "corner": "typ"}


def calls(log) -> list:
    """The stand-ins' recorded calls."""
    if not os.path.isfile(log):
        return []
    with open(log) as f:
        return [json.loads(ln) for ln in f if ln.strip()]
