"""`emir verify-sim`: prove, on the machine that uses it, that a simulator's
route into EMIR gives the right numbers.

Support for a simulator is proven where it runs, never claimed from
somewhere else. Each simulator (Spectre, HSPICE, Xyce, ngspice) names its
currents its own way, writes its own file format and has its own sign
conventions; a new release, a site's wrapper script or an option file can
change any of them. So the local agent (or the person) runs

    emir verify-sim --sim xyce               # open simulators run directly
    emir verify-sim --sim spectre            # licensed: the plan, nothing runs
    emir verify-sim --sim spectre --run      # ... after the person's go-ahead

once per machine, simulator version and configuration. It builds small
testbenches whose answers are known by hand, simulates them through the same
route a real run takes (`emir simulate`: the save lines, the names sidecar),
reads the results with the normal reader, runs rcc on them, compares
everything with the analytic answers, and records the outcome (an
`emir.verify_sim/1` record) in the state directory (`$EMIR_STATE_DIR`, else
`~/.emir`; `verified.json`).

The testbenches are the bundled sample rail (`emir/data/sample/rail.dspf`):
a 60 um metal1 rail of twelve 1-ohm segments fed at x = 0 (VDD, 1.2 V), with
four contacts at x = 15, 30, 45, 60 um (Rc0..Rc2: 12 ohm, two cuts; Rc3:
24 ohm, one cut). What hangs on the contacts makes three cases:

* **dc** -- ideal current sources ILOAD0..3, each drawing 1 mA. Branch
  currents: Rm1..Rm3 4 mA, Rm4..Rm6 3 mA, Rm7..Rm9 2 mA, Rm10..Rm12 1 mA,
  Rc0..Rc3 1 mA, all flowing from the feed towards the loads (positive
  `R:1`). With the sample's process file (metal1 3.2 mA/um avg on 0.75 um:
  2.4 mA) the worst ratio is 4 / 2.4 = 1.6667 (avg, Rm1..Rm3); the IR drop at
  the last contact is 3 x 1 ohm x (4 + 3 + 2 + 1) mA + 24 ohm x 1 mA =
  54.0 mV. Avg, rms and peak are equal.
* **pulse** -- the RC variant (twelve 1 fF caps to ground) with each load a
  trapezoid from 0 to 2 mA (PULSE 0 2m, delay 1 ns, edges 0.1 ns, top 4.9 ns,
  period 10 ns), all in phase, over four periods. Per load: avg = 2 mA x
  (4.9 + 0.1) / 10 = 1.0 mA, peak 2 mA, rms = 2 mA x sqrt((4.9 + 0.2 / 3) /
  10) = 1.409492 mA. So Rm1 carries avg 4 mA (ratio 1.6667), peak 8 mA
  against 16 x 0.75 = 12 mA (0.6667), rms 5.637969 mA against sqrt(28.8 x 5)
  x 0.75 = 9 mA (0.62644); Rc3 avg 1 / 0.75 = 1.3333, peak 2 / 2.4 = 0.8333,
  rms 1.409492 / 1.5 = 0.93966.
* **dev** -- real devices on the rail, each fed by an ideal 1 mA DC current
  source (built-in level-1 models, no PDK): a diode-connected PMOS MP0 at
  contact 0, a diode-connected NMOS MN1 at contact 1, a resistor RL2 at
  contact 2 and an NMOS wrapped in a subcircuit (XM3) at contact 3. Extra
  contacts to the bulk and the gates (Rb0, Rg1, Rg3) carry no current, so the
  rail carries the dc case's currents: the same branch currents (the extra
  contacts 0), worst ratio and IR drop.

Each case is the RC netlist with every branch current saved (rcc), and every
branch current is compared with its known value and direction. Tolerances:
currents, avg and peak ratios and IR drops to 1e-3 of the analytic value
(HSPICE 9601 and Spectre's default %g keep 5 to 6 digits; a current whose
answer is 0 to 1e-6 A, 1e-3 of the loads); rms ratios to 1e-2 (the
simulator's time steps on 0.1 ns edges).

**What a verification covers is per kind of current** (`KINDS`): this
release reads resistor branch currents (rcc) alone, so a record covers
`branch` when its checks of that kind passed (`covers`; a record without the
list covers no kind). Not covered: Spectre's PSS, other output formats (CSDF,
Spectre's nutascii: `format not covered`, EMIR-W464 with no verify-sim to
run, since none can cover them), and any simulator argument or option file
other than the ones given (`--sim-args`).

**The records** (`verified.json`, newest last). A run counts its result as
verified only when the LATEST valid record of this machine for the same key
-- simulator, version (`version_key`), output format and simulator arguments
(`--sim-args`, normalised) -- passed with this emir's minor version: a later
FAIL cancels an earlier PASS, a pass of another emir minor version is
`stale`. Every record is validated strictly (`record_problem`): an invalid
one counts as no record (EMIR-W415). A result whose version cannot be told
(its header, else the names sidecar `emir simulate` writes) is never matched
(`version unknown`, EMIR-W416). The host name is kept only with
`--record-host`; a record that names another host does not count here.

The file is written under a lock (`verified.json.lock`, POSIX `flock`) and
replaced atomically (a temporary file in the same directory, then
`os.replace`), so two verifications that finish together both keep their
records. A file that exists but cannot be read as `emir.verified/1` (cut
short, not JSON) is never treated as empty and overwritten: runs say so
(EMIR-W415, `records_problem`), and the next `emir verify-sim` moves it
aside to `verified.json.bad-<date>` before it writes. One this user cannot
read (its permissions) is not damaged: it is left alone; runs say `records
unreadable` and suggest no verification (`emir next` asks the person for
access instead), and `emir verify-sim` refuses to start (EMIR-E102) rather
than simulate for a record it could not keep (EMIR-W467). A result whose
version is unknown gets no verification command either: no record can match
it. A save keeps the file's mode.
"""
from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import re
import shlex
import socket
import sys
import tempfile

from .errors import EmirError, collecting, notice
from .output import command_line, document, parser, strip_options, write_text

SIMS = ("spectre", "hspice", "xyce", "ngspice")
LICENSED = ("spectre", "hspice")
#: the output format each simulator's route writes (what a record is for)
FORMAT = {"spectre": "psfascii", "hspice": "tr0ascii", "xyce": "nutascii",
          "ngspice": "nutascii"}
STATE_ENV = "EMIR_STATE_DIR"
RECORDS = "verified.json"
SCHEMA = "verify_sim"
RECORDS_SCHEMA = "verified"
#: what a record names itself (the document `emir verify-sim` prints is the same schema)
RECORD_SCHEMA = f"emir.{SCHEMA}/1"
#: the fields every record must hold (`record_problem`)
RECORD_REQUIRED = ("schema", "simulator", "version", "format", "emir_version", "date",
                   "passed", "checks")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
#: the kinds of current a run reads (module doc), and what each is, for a person
KINDS = {"branch": "resistor branch currents (rcc)"}


#: the loads (their DSPF pins replace the sample's MP0:s .. MP3:s)
LOADS = 4
SUPPLY_V = 1.2
PERIOD = 10e-9
TSTOP = 40e-9
#: analytic answers (see the module doc)
DC = {"branch_A": {**{f"Rm{k}": 4e-3 for k in (1, 2, 3)}, **{f"Rm{k}": 3e-3 for k in (4, 5, 6)},
                   **{f"Rm{k}": 2e-3 for k in (7, 8, 9)},
                   **{f"Rm{k}": 1e-3 for k in (10, 11, 12)},
                   **{f"Rc{k}": 1e-3 for k in range(LOADS)}},
      "pin_A": 1e-3, "worst_ratio": 4 / 2.4, "ir_drop_mV": 54.0}
_RMS = 2e-3 * ((4.9 + 0.2 / 3) / 10) ** 0.5
PULSE = {"load_avg_A": 1e-3, "load_peak_A": 2e-3, "load_rms_A": _RMS,
         "Rm1": {"avg": 4e-3 / 2.4e-3, "peak": 8e-3 / 12e-3, "rms": 4 * _RMS / 9e-3},
         "Rc3": {"avg": 1e-3 / 0.75e-3, "peak": 2e-3 / 2.4e-3, "rms": _RMS / 1.5e-3}}
#: the dev case's extra contacts (no current) and the pins they reach
DEV_STUBS = {"Rb0": ("VDD:3", "MP0:b", 15.0, 0.75), "Rg1": ("VDD:6", "MN1:g", 30.0, 0.75),
             "Rg3": ("VDD:12", "XM3:g", 60.0, 0.75)}
TOL = {"current": 1e-3, "ratio": 1e-3, "rms": 1e-2, "ir": 1e-3, "zero_A": 1e-6}
def state_dir() -> str:
    """Where EMIR keeps what it learns about this machine: $EMIR_STATE_DIR,
    else ~/.emir."""
    return os.path.abspath(os.path.expanduser(os.environ.get(STATE_ENV) or "~/.emir"))


def records_path() -> str:
    return os.path.join(state_dir(), RECORDS)


def read_records(path: str | None = None) -> tuple:
    """`(every entry of the records file in file order, problem)`: problem is
    None, or why a file that exists cannot be read as `emir.verified/1` (cut
    short, not JSON, no `records` list) -- then its entries are []. No file
    is no records and no problem: a missing record is "not verified", never
    an error."""
    recs, problem, _damaged = _read(path or records_path())
    return recs, problem


def _read(path: str) -> tuple:
    """`read_records`, and whether a problem is damage (the file was read
    and is not a records document) rather than a file this user cannot read
    (its permissions: another user's file in a shared state directory)."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return [], None, False
    except UnicodeError as e:
        return [], f"cannot be read ({e})", True
    except OSError as e:
        return [], f"cannot be read ({e.strerror or e})", False
    try:
        doc = json.loads(text)
    except ValueError as e:
        return [], f"is not JSON ({e})", True
    recs = doc.get("records") if isinstance(doc, dict) else None
    if not isinstance(recs, list):
        return [], "is not an emir.verified/1 document (no `records` list)", True
    return list(recs), None, False


def records_unreadable(path: str | None = None) -> str | None:
    """Why this user cannot read the records file (its permissions: another
    user's file in a shared state directory), or None: no file, a readable
    one, or a damaged one (which the next `emir verify-sim` moves aside).
    Then no record counts here and no verification can keep its own, so
    none is suggested (`status`) or run (`cmd_verify_sim`, EMIR-E102)."""
    _recs, problem, damaged = _read(path or records_path())
    return problem if problem and not damaged else None


def load_records(path: str | None = None) -> list:
    """Every entry of the records file, valid or not, in file order ([] when
    there are none, or when the file is damaged: `read_records` says so).
    `valid_records` keeps the ones that count."""
    return read_records(path)[0]


def valid_records(path: str | None = None) -> tuple:
    """`(valid records in file order, [(index, problem)] of the others)`."""
    good, bad = [], []
    for k, r in enumerate(load_records(path)):
        why = record_problem(r)
        if why:
            bad.append((k, why))
        else:
            good.append(r)
    return good, bad


@contextlib.contextmanager
def _locked(path: str):
    """An exclusive lock on `<path>.lock` (POSIX `flock`) for the
    read-append-replace of the records file; where `fcntl` does not exist
    (Windows) the write is still atomic, only not serialised."""
    try:
        import fcntl
    except ImportError:                                 # pragma: no cover - Windows
        yield
        return
    with open(path + ".lock", "a", encoding="utf-8") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN)


def _mode(path: str) -> int:
    """The mode `path` keeps when it is replaced: its own, or for a new file
    what `open` would give it (0666 less the umask)."""
    try:
        return os.stat(path).st_mode & 0o7777
    except OSError:
        um = os.umask(0)
        os.umask(um)
        return 0o666 & ~um


def _replace(path: str, text: str):
    """Write `text` to `path` atomically: a temporary file in the same
    directory, flushed to disk, then `os.replace` (a reader sees the old file
    or the new one, never half of one). The file keeps its mode (`_mode`:
    the temporary file is created 0600)."""
    d = os.path.dirname(path)
    mode = _mode(path)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=d)
    try:
        with contextlib.suppress(AttributeError, OSError):
            os.chmod(tmp, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def save_record(rec: dict) -> str | None:
    """Append `rec` to the records file (every entry kept as it was, invalid
    ones included: they are reported, never silently dropped), under the
    lock and atomically (module doc). A damaged file (read, and not a
    records document) is moved aside to `verified.json.bad-<date>` first
    (EMIR-W415), never overwritten; one this user cannot read is left as it
    is. None (EMIR-W467) when the record cannot be written."""
    path = records_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _locked(path):
            recs, problem, damaged = _read(path)
            if problem and not damaged:
                notice("EMIR-W467", f"the verification record was not kept: {path} "
                       f"{problem}: it is left as it is (its records may be another "
                       f"user's); give this user read access to it, or set EMIR_STATE_DIR "
                       f"to a directory of its own", _stderr)
                return None
            if problem:
                stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
                    "%Y%m%dT%H%M%SZ")
                aside = f"{path}.bad-{stamp}"
                os.replace(path, aside)
                notice("EMIR-W415", f"{path} {problem}: it was moved aside to {aside}, and "
                       f"the records it held count as none; this verification starts a new "
                       f"file", _stderr)
            doc = document(RECORDS_SCHEMA, records=recs + [rec])
            _replace(path, json.dumps(doc, indent=1) + "\n")
    except OSError as e:
        notice("EMIR-W467", f"the verification record was not kept: cannot write {path}: "
               f"{e.strerror or e}", _stderr)
        return None
    return path


def version_key(text: str | None) -> str | None:
    """What two version strings of one simulator are compared by: the first
    three parts of the first dotted number (`19.1.0` in a PSF header's
    `19.1.0.237.isr4 64bit`, `7.10.0` in Xyce's development string), else the
    first number (`45` in `ngspice-45+ : ...`). The same release, whichever
    way the simulator spelled it (its version flag, the result's header).
    A version of nothing but zeros (`0`, `0.0`: a placeholder some files
    carry, no release) is no version (None)."""
    if not text or not isinstance(text, str):
        return None
    m = re.search(r"\d+(?:\.\d+)+", text)
    if m:
        key = ".".join(m.group(0).split(".")[:3])
    else:
        m = re.search(r"\d+", text)
        key = m.group(0) if m else None
    if key is None or not key.strip("0."):
        return None
    return key


def args_key(text) -> str:
    """The simulator arguments a record or a result is for, normalised:
    split as a shell would and joined by single blanks ("" for none). The
    order is kept: two orders are two configurations."""
    if not text:
        return ""
    try:
        return " ".join(shlex.split(str(text)))
    except ValueError:
        return " ".join(str(text).split())


def minor(version) -> tuple | None:
    """`(major, minor)` of an emir version string, or None."""
    m = re.match(r"^\s*(\d+)\.(\d+)", str(version or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def this_host() -> str:
    return socket.gethostname()


def record_problem(rec) -> str | None:
    """Why `rec` is not a valid `emir.verify_sim/1` record, or None. Strict:
    every required field present and of its type, `passed` a JSON boolean
    that agrees with the checks (true exactly when there is at least one and
    every one is ok), a passing record with its simulator's version, the
    format the simulator's route writes, a date as emir writes it."""
    if not isinstance(rec, dict):
        return "not a JSON object"
    missing = [k for k in RECORD_REQUIRED if k not in rec]
    if missing:
        return f"no {', '.join(missing)}"
    if rec["schema"] != RECORD_SCHEMA:
        return f"schema {rec['schema']!r}, not {RECORD_SCHEMA!r}"
    sim = rec["simulator"]
    if sim not in SIMS:
        return f"simulator {sim!r} is not one of {', '.join(SIMS)}"
    if not isinstance(rec["passed"], bool):
        return f"passed is {rec['passed']!r}, not a JSON boolean"
    if rec["format"] != FORMAT[sim]:
        return f"format {rec['format']!r}: {sim}'s route writes {FORMAT[sim]}"
    if rec["version"] is not None and not isinstance(rec["version"], str):
        return "version is not a string"
    if not isinstance(rec["emir_version"], str) or minor(rec["emir_version"]) is None:
        return f"emir_version {rec['emir_version']!r} is not an emir version"
    if not isinstance(rec["date"], str) or not _DATE.match(rec["date"]):
        return f"date {rec['date']!r} is not YYYY-MM-DDTHH:MM:SSZ"
    checks = rec["checks"]
    if not isinstance(checks, list) or not all(
            isinstance(c, dict) and isinstance(c.get("name"), str)
            and isinstance(c.get("ok"), bool) for c in checks):
        return "checks is not a list of {name, ok}"
    if rec["passed"] != (bool(checks) and all(c["ok"] for c in checks)):
        return "passed disagrees with its checks"
    if rec["passed"] and not version_key(rec["version"]):
        return "a passing record without its simulator's version"
    for k in ("host", "sim_args"):
        if rec.get(k) is not None and not isinstance(rec[k], str):
            return f"{k} is not a string"
    if rec.get("covers") is not None:
        cov = rec["covers"]
        if not isinstance(cov, list) or not all(isinstance(k, str) for k in cov):
            return "covers is not a list of kinds"
        # a kind is covered only by checks of that kind that passed: a record
        # cannot claim a kind its checks never looked at (a kind this release
        # does not read is kept and counts for nothing)
        shown = {c.get("kind") for c in checks if c["ok"]}
        unbacked = [k for k in cov if k in KINDS and k not in shown]
        if unbacked:
            return f"covers {', '.join(unbacked)} with no passing check of that kind"
    return None


def record_covers(rec: dict | None) -> list:
    """The kinds of current of this release a valid record's checks cover
    ([] for a record without `covers`: it verifies the route, no kind)."""
    return [k for k in (rec or {}).get("covers") or [] if k in KINDS]


def coverable(sim: str) -> list:
    """The kinds a passing `emir verify-sim --sim <sim>` of this emir covers:
    the branch currents."""
    return sorted(KINDS)


def record_key(rec: dict) -> tuple:
    """What a record verifies: `(simulator, version key, format, arguments)`."""
    return (rec["simulator"], version_key(rec["version"]), rec["format"],
            args_key(rec.get("sim_args")))


def _counts_here(rec: dict) -> bool:
    """A record that names a host counts only on that host."""
    return not rec.get("host") or rec["host"] == this_host()


def _judge(rec: dict | None, other_host: bool = False) -> tuple:
    """`(status, reason)` of the latest record of a key."""
    if rec is None:
        return "not verified", ("other host" if other_host else "no record")
    if not rec["passed"]:
        return "not verified", "failed"
    if minor(rec["emir_version"]) != minor(_ver()):
        return "not verified", "stale"
    return "verified", None


def lookup(sim: str, version: str | None, fmt: str | None, sim_args=None,
           records: list | None = None) -> dict:
    """The verdict for a result: `{status, reason, record}`. The latest
    valid record of this machine for the key (simulator, version, format,
    simulator arguments) decides; no version, no match."""
    want = version_key(version)
    if want is None:
        return {"status": "not verified", "reason": "version unknown", "record": None}
    key = (sim, want, fmt, args_key(sim_args))
    latest, other = None, False
    for r in records if records is not None else valid_records()[0]:
        if record_key(r) != key:
            continue
        if not _counts_here(r):
            other = True
            continue
        latest = r
    status_, reason = _judge(latest, other)
    return {"status": status_, "reason": reason, "record": latest}


def routes(sim: str, fmt: str | None = None, sim_args=None) -> list:
    """Every version of `sim` this machine has a record for (in `fmt`, with
    `sim_args`), oldest first: `[{version, status, reason, record}]` from the
    latest valid record of each (what `emir info` and `emir next` show)."""
    latest: dict = {}
    for r in valid_records()[0]:
        if r["simulator"] != sim or (fmt and r["format"] != fmt) \
                or args_key(r.get("sim_args")) != args_key(sim_args) or not _counts_here(r):
            continue
        k = record_key(r)
        latest.pop(k, None)                  # re-inserted: the order is the latest's
        latest[k] = r
    out = []
    for k, r in latest.items():
        st, reason = _judge(r)
        out.append({"version": k[1], "status": st, "reason": reason, "record": r})
    return out


def missing_covers(rec: dict | None, sim: str) -> list:
    """The kinds of current a verification of `sim` by this emir covers
    (`coverable`) that a record does not: all of them for a record made
    before records said what they cover."""
    have = set(record_covers(rec))
    return [k for k in coverable(sim) if k not in have]


def verified_route(sim: str, fmt: str | None = None) -> dict | None:
    """The newest record that verifies some version of `sim` (in `fmt`,
    without simulator arguments) on this machine, for every kind of current
    this emir's verification covers (`missing_covers` empty), or None: what
    `emir next` and `emir info` count before a run says which kinds it read."""
    ok = [x["record"] for x in routes(sim, fmt)
          if x["status"] == "verified" and not missing_covers(x["record"], sim)]
    return ok[-1] if ok else None


# ------------------------------------------------------------------ the run's lookup


def detect(w=None, sim_hint: str | None = None) -> dict:
    """Which simulator wrote a result, from its header (PSF ASCII, ngspice's
    nutascii, HSPICE ASCII, CSDF), else its names sidecar, else `sim_hint`
    (`emir run --sim`); its version (header, else sidecar) and the simulator
    arguments the sidecar of `emir simulate` records."""
    meta = (w if isinstance(w, dict) else getattr(w, "meta", None)) or {}
    sim = meta.get("simulator")
    how = (meta.get("simulator_from") or "result header") if sim else None
    if not sim and sim_hint:
        sim, how = sim_hint, "--sim"
    known = isinstance(meta.get("sim_args"), str)
    return {"simulator": sim if sim in SIMS else (sim or None),
            "version": meta.get("simulator_version"), "format": meta.get("format"),
            "detected_from": how, "sim_args": meta.get("sim_args") if known else None,
            "sim_args_known": known}


def _what(d: dict) -> str:
    return " ".join(x for x in (d.get("simulator") or "", version_key(d.get("version")) or "",
                                f"({d['format']})" if d.get("format") else "",
                                f"[{args_key(d['sim_args'])}]" if d.get("sim_args") else "")
                    if x)


def _kinds_text(kinds: dict, which) -> str:
    """`diode terminals (D5:k), BJT terminals (2: Q1:e, Q2:e)` for `which`."""
    parts = []
    for k in which:
        pins = kinds.get(k) or []
        ex = ", ".join(pins[:3]) + (" ..." if len(pins) > 3 else "")
        parts.append(f"{KINDS[k]} ({len(pins)}: {ex})" if len(pins) > 1 else
                     f"{KINDS[k]} ({ex})")
    return "; ".join(parts)


def status(w=None, sim_hint: str | None = None, applicable: bool = True,
           pins: dict | None = None) -> dict:
    """The run summary's `simulator_verification`: `verified`, `partly
    verified` (the route is, some kinds of current the run reads are not),
    `not verified` (with its `reason`), `unknown simulator`, or `not
    applicable` (no simulator wrote the currents). `pins` is the run's
    `{kind: [names]}` (`run_pins`; None: not known, the route alone is
    looked up, as `emir next` and `emir info` do)."""
    base = {"status": None, "reason": None, "simulator": None, "version": None,
            "format": None, "detected_from": None, "sim_args": None,
            "sim_args_known": False, "version_checked": False, "record": None,
            "records_file": records_path(), "ignored_records": 0, "records_problem": None,
            "current_kinds": None, "command": None, "note": None}
    if not applicable:
        return {**base, "status": "not applicable",
                "note": "the currents did not come from a simulator result"}
    d = detect(w, sim_hint)
    out = {**base, **d}
    sim = d["simulator"]
    if sim not in SIMS:
        out.update(status="unknown simulator",
                   note=(f"the result names {sim!r}, which emir verify-sim does not cover"
                         if sim else "neither the result's header nor its names sidecar "
                                     "says which simulator wrote it; pass --sim"))
        return out
    recs, bad = valid_records()
    out["ignored_records"] = len(bad)
    out["records_problem"] = read_records()[1]
    unreadable = records_unreadable()
    if d["format"] and d["format"] != FORMAT[sim]:
        # no record can match: verify-sim writes and checks FORMAT[sim] only
        out.update(status="not verified", reason="format not covered", command=None,
                   note=(f"emir verify-sim covers {sim}'s {FORMAT[sim]} output only: this "
                         f"{d['format']} result stays UNVERIFIED on this machine (simulate "
                         f"through `emir simulate --sim {sim}`, which writes {FORMAT[sim]}, "
                         f"for a route that can be verified)"))
        return out
    out["command"] = command_line("verify-sim", "--sim", sim,
                                  *(("--sim-args", d["sim_args"]) if d["sim_args"] else ()))
    lk = lookup(sim, d["version"], d["format"], d["sim_args"], records=recs)
    rec = lk["record"]
    out.update(status=lk["status"], reason=lk["reason"], record=_brief(rec) if rec else None,
               version_checked=lk["status"] == "verified")
    what = _what(d)
    uncovered, never = [], []
    if pins is not None:
        cov = set(record_covers(rec)) if lk["status"] == "verified" else set()
        can = set(coverable(sim))
        out["current_kinds"] = [{"kind": k, "what": KINDS[k], "count": len(v),
                             "examples": list(v[:4]), "covered": k in cov,
                             "verify_sim_covers": k in can}
                            for k, v in sorted(pins.items()) if v]
        uncovered = [x["kind"] for x in out["current_kinds"] if not x["covered"]]
        never = [k for k in uncovered if k not in can]
    if lk["status"] == "verified" and uncovered:
        covered = [x["kind"] for x in out["current_kinds"] if x["covered"]]
        redo = [k for k in uncovered if k not in never]
        out.update(status="partly verified" if covered else "not verified",
                   reason="current kinds not covered", version_checked=bool(covered),
                   command=out["command"] if redo else None)
        out["note"] = (
            (f"verified for its {', '.join(KINDS[k] for k in covered)}; " if covered else "")
            + f"NOT for its {_kinds_text(pins, uncovered)}"
            + (": emir verify-sim does not cover " + ", ".join(KINDS[k] for k in never)
               + ", so those currents stay UNVERIFIED on this machine" if never else "")
            + (("; " if never else ": ") + "the record of "
               + f"{rec.get('date')} does not cover " + ", ".join(KINDS[k] for k in redo)
               + ": verify again with this emir" if redo else ""))
        return out
    if lk["status"] == "verified":
        out["note"] = (None if d["sim_args_known"] else
                       "the result does not say which simulator arguments wrote it: matched "
                       "the verification made without --sim-args")
        out["command"] = None
        return out
    if unreadable:
        # no record counts, and a verification could not keep its own
        # (EMIR-W467): no verify-sim is suggested, the person's access is
        out.update(reason="records unreadable", command=None,
                   note=(f"the records file {records_path()} {unreadable}: this user may "
                         f"not read it, so no verification of {what} counts here (give this "
                         f"user read access to it, or set EMIR_STATE_DIR to a directory of "
                         f"its own; then emir verify-sim --sim {sim})"))
        return out
    if lk["reason"] == "version unknown":
        # no record can ever match this result: no verify-sim is suggested
        out["command"] = None
        out["note"] = (f"not verified: version unknown (neither the result's header nor a "
                       f"names sidecar of emir simulate says which {sim} version wrote it)")
    elif lk["reason"] == "failed":
        out["note"] = (f"the latest emir verify-sim of {what} on this machine FAILED "
                       f"({rec.get('date')})")
    elif lk["reason"] == "stale":
        out["note"] = (f"the passing emir verify-sim of {what} is from emir "
                       f"{rec.get('emir_version')} ({rec.get('date')}); this is emir {_ver()}: "
                       f"verify again with this version")
    elif lk["reason"] == "other host":
        out["note"] = (f"the records of {what} name another host: they do not count on "
                       f"{this_host()}")
    else:
        out["note"] = f"no emir verify-sim record of {what} on this machine"
    if never:
        out["note"] += (f"; emir verify-sim does not cover its {_kinds_text(pins, never)}: "
                        f"those currents stay UNVERIFIED whatever it finds")
    return out


def _brief(rec: dict) -> dict:
    return {k: rec.get(k) for k in ("simulator", "version", "format", "date", "passed",
                                    "emir_version", "host", "sim_args", "covers")}


def report_status(st: dict, log=None) -> None:
    """The notice a run carries for its `simulator_verification` (one per
    run), and EMIR-W415 when the records file holds invalid records or
    cannot be read at all."""
    log = log or _stderr
    s, reason, sim = st.get("status"), st.get("reason"), st.get("simulator")
    if st.get("records_problem"):
        damaged = _read(st.get("records_file") or records_path())[2]
        notice("EMIR-W415", f"{st.get('records_file')} {st['records_problem']}: every record "
               f"in it counts as none; it is left as it is, and "
               + (f"the next emir verify-sim moves it aside (run emir verify-sim --sim {sim} "
                  f"again)" if damaged else
                  "emir verify-sim leaves it too (this user may not read it: give this "
                  "user read access, or set EMIR_STATE_DIR to a directory of its own)"),
               log)
    if st.get("ignored_records"):
        notice("EMIR-W415", f"{st['ignored_records']} record(s) of {st.get('records_file')} "
               f"are not valid emir.verify_sim/1 records and count as none (run emir "
               f"verify-sim --sim {sim} again)", log)
    if s == "not verified" and reason == "failed":
        notice("EMIR-W414", f"{sim} route FAILED its verification on this machine: "
               f"{st['note']}; do not rely on these numbers", log)
    elif s == "not verified" and reason == "version unknown":
        notice("EMIR-W416", f"{sim} route not verified for this result: {st['note']}; "
               f"simulate with emir simulate (it records the version)", log)
    elif s in ("not verified", "partly verified") and reason in ("format not covered",
                                                                 "current kinds not covered"):
        notice("EMIR-W464", f"{sim} route {s} on this machine: {st['note']}"
               + (f" (run {st['command']})" if st.get("command") else ""), log)
    elif s == "not verified" and reason == "records unreadable":
        notice("EMIR-W464", f"simulator route not verified on this machine: {st['note']}",
               log)
    elif s == "not verified":
        notice("EMIR-W464", f"simulator route not verified on this machine: run "
               f"{st.get('command') or 'emir verify-sim --sim ' + str(sim)} ({st['note']})",
               log)
    elif s == "verified":
        notice("EMIR-I463", f"{sim} route verified on this machine "
               f"({(st.get('record') or {}).get('date')}"
               + ("" if st.get("sim_args_known") else "; simulator arguments not recorded "
                                                      "with the result")
               + ")", log)
    elif s == "unknown simulator":
        notice("EMIR-I466", st["note"], log)


def run_pins(d, solved: dict) -> dict | None:
    """`{kind: [names]}` of the currents a run read from its result: the
    resistor branch currents of every rcc net (`branch`). None when no net
    read a result (or the run's nets are not known)."""
    if d is None or not solved:
        return None
    branches = []
    for net, r in solved.items():
        mode = r.get("mode") if isinstance(r, dict) else None
        try:
            if mode == "rcc":
                branches += [f"{x['name']}:1" for x in d.net_resistors(net)]
        except (AttributeError, KeyError, TypeError):
            continue
    return {"branch": branches} if branches else None


def run_status(a, w=None, res=None) -> dict:
    """`simulator_verification` for a run (`dspf.analyse`): from the result's
    header and names sidecar (`waves.peek`: the data is not read again),
    with what the read result says filling any gap; the kinds of current the
    run read from `res` (its DSPF and solved nets, `run_pins`); not
    applicable for DC currents."""
    if not getattr(a, "cc_sim", None):
        return status(applicable=False)
    if getattr(a, "_verifying", False):
        return {**status(applicable=False), "note": "a run of emir verify-sim itself"}
    from . import waves
    meta = dict(waves.peek(a.cc_sim, getattr(a, "analysis", None)))
    for k, v in ((getattr(w, "meta", None) or {}).items() if w is not None else ()):
        if k in ("simulator", "simulator_version", "format", "simulator_from") \
                and not meta.get(k) and v:
            meta[k] = v
    sim = getattr(a, "sim", None)
    if sim is None and getattr(a, "netlist", None):
        sim = "spectre"                       # --netlist runs Spectre unless --sim says
    pins = run_pins(getattr(res, "dspf", None),
                    getattr(res, "solved", None)) if res is not None else None
    return status(meta, sim_hint=sim, pins=pins)


# ------------------------------------------------------------------ the testbench


def _sample_lines(load: str = "ILOAD", pin: str = "p") -> tuple:
    """The sample DSPF's header lines and element lines, with the loads
    renamed MPk:s -> ILOADk:p."""
    from .selftest import sample_paths
    with open(sample_paths()[0], encoding="utf-8") as f:
        text = f.read()
    for k in range(LOADS):
        text = text.replace(f"MP{k}:s MP{k} s", f"{load}{k}:{pin} {load}{k} {pin}")
        text = text.replace(f"MP{k}:s", f"{load}{k}:{pin}")
    head, res, caps = [], [], []
    for ln in text.splitlines():
        if ln.startswith("*|"):
            head.append(ln)
        elif ln[:1] in ("R", "r"):
            res.append(ln)
        elif ln[:1] in ("C", "c"):
            caps.append(ln)
    return head, res, caps


#: the dev case's pin at each contact (the sample's ILOADk:p)
_DEV_AT = {0: ("MP0:s", "MP0 s"), 1: ("MN1:d", "MN1 d"), 2: ("RL2:2", "RL2 2"),
           3: ("XM3:d", "XM3 d")}


def _dev_lines() -> tuple:
    """The dev case's header and resistor lines: the sample rail with the
    devices' pins on its contacts, and the no-current contacts to the other
    pins (bulk, gates) beside them."""
    head, res, _caps = _sample_lines()
    swap = {f"ILOAD{k}:p ILOAD{k} p": f"{pin} {inst}" for k, (pin, inst) in _DEV_AT.items()}
    swap.update({f"ILOAD{k}:p": pin for k, (pin, _i) in _DEV_AT.items()})

    def sub(ln):
        for a_, b_ in swap.items():
            ln = ln.replace(a_, b_)
        return ln
    head, res = [sub(x) for x in head], [sub(x) for x in res]
    for name, (node, pin, x, y) in DEV_STUBS.items():
        inst, _, term = pin.partition(":")
        head.append(f"*|I ({pin} {inst} {term} I 0 {x:.4f} {y:.4f})")
        res.append(f"{name} {node} {pin} 12.000000 $cont $A=0.033800 $VC=2 "
                   f"$LLX={x - 0.375:.4f} $LLY={y - 0.375:.4f} $URX={x + 0.375:.4f} "
                   f"$URY={y + 0.375:.4f} $lvl=0")
    return head, res


def verify_dspf(rc: bool, dev: bool = False, load: str = "ILOAD", pin: str = "p") -> str:
    """The DSPF EMIR reads: the sample rail (with its caps when `rc`; with
    the devices' pins when `dev`; its loads named `<load>k:<pin>`)."""
    head, res, caps = _sample_lines(load, pin)
    if dev:
        head, res = _dev_lines()
        caps = []
    hdr = [ln.replace('"railblock"', '"emir_verify_rail"') for ln in head
           if not ln.startswith(("*|NET", "*|P", "*|S", "*|I"))]
    net = [ln for ln in head if ln.startswith(("*|NET", "*|P", "*|S", "*|I"))]
    what = ("devices on its contacts (MP0, MN1, RL2, XM3)" if dev
            else "its loads ILOAD0..3 (ideal current sources)")
    return "\n".join(hdr + [f"* emir verify-sim: the sample rail, {what}",
                            ".SUBCKT emir_verify_rail VDD"]
                     + net + res + (caps if rc else []) + [".ENDS emir_verify_rail"]) + "\n"


def _clean(ln: str) -> str:
    """An element line without its DSPF $ annotations."""
    return " ".join(t for t in ln.split() if not t.startswith("$"))


def _load(k: int, node: str, pulse: bool) -> str:
    if pulse:
        return f"ILOAD{k} {node} 0 PULSE(0 2m 1n 0.1n 0.1n 4.9n 10n)"
    return f"ILOAD{k} {node} 0 DC 1m"


#: the dev case's models and the subcircuit-wrapped device (built-in level 1)
DEV_MODELS = [".model emirpmos pmos level=1 vto=-0.4 kp=2e-4",
              ".model emirnmos nmos level=1 vto=0.4 kp=2e-4",
              ".subckt emirnfet d g s b",
              "M0 d g s b emirnmos W=100u L=1u",
              ".ends emirnfet"]


def dev_devices(node) -> list:
    """The dev case's devices and their 1 mA sources; `node(pin)` is the node
    a DSPF pin sits on (the pin node itself in the RC netlist)."""
    return [f"MP0 emir_p0 emir_p0 {node('MP0:s')} {node('MP0:b')} emirpmos W=100u L=1u",
            "IB0 emir_p0 0 DC 1m",
            f"MN1 {node('MN1:d')} {node('MN1:g')} emir_n1 0 emirnmos W=100u L=1u",
            "IB1 emir_n1 0 DC 1m",
            f"RL2 emir_r2 {node('RL2:2')} 100",
            "IB2 emir_r2 0 DC 1m",
            f"XM3 {node('XM3:d')} {node('XM3:g')} emir_n3 0 emirnfet",
            "IB3 emir_n3 0 DC 1m"]


def rail_netlist(rc: bool, pulse: bool, dev: bool = False) -> str:
    """The RC netlist a simulator runs for rcc: the rail's resistors (and
    caps), the loads (or the devices) at the pins (SPICE syntax, no $
    annotations)."""
    _head, res, caps = _sample_lines()
    lines = ["* emir verify-sim: the RC netlist (the rail and its loads)"]
    if dev:
        _h, res = _dev_lines()
        lines += DEV_MODELS
    lines.append(".SUBCKT emir_verify_rail VDD")
    lines += [_clean(r) for r in res] + ([_clean(c) for c in caps] if rc else [])
    if dev:
        lines += dev_devices(lambda pin: pin)
    else:
        lines += [_load(k, f"ILOAD{k}:p", pulse) for k in range(LOADS)]
    return "\n".join(lines + [".ENDS emir_verify_rail"]) + "\n"


def spice_testbench(sim: str, netlist: str, title: str, tran: str = ".tran 10p 40n",
                    options=()) -> str:
    """The testbench for Xyce, ngspice and HSPICE (SPICE syntax); `options`:
    lines before the analysis."""
    inc = f".include '{netlist}'" if sim == "hspice" else f".include {netlist}"
    return "\n".join([f"* emir verify-sim ({sim}): {title}", inc,
                      f"VDD vdd 0 DC {SUPPLY_V}", "XI0 vdd emir_verify_rail",
                      *options, tran, ".end"]) + "\n"


def spectre_testbench(netlist_text: str, title: str) -> str:
    """The testbench for Spectre: Spectre syntax for the analysis; the
    circuit in SPICE syntax after `simulator lang=spice` (Spectre Circuit
    Simulator Reference 19.1: `Name tran stop=...`, `simulator lang`)."""
    return "\n".join([f"// emir verify-sim (spectre): {title}", "simulator lang=spice",
                      netlist_text.rstrip("\n"), f"VDD vdd 0 DC {SUPPLY_V}",
                      "XI0 vdd emir_verify_rail", "simulator lang=spectre",
                      "emirtran tran stop=40n maxstep=10p"]) + "\n"


#: the three simulations: (case, rc, pulse), each the RC netlist read in rcc
CASES = (("dc", False, False), ("pulse", True, True), ("dev", False, False))


def _dspf_name(case: str, rc: bool) -> str:
    return "verify_dev.dspf" if case == "dev" else f"verify_{'rc' if rc else 'r'}.dspf"


def build(sim: str, out: str) -> list:
    """Write the DSPFs, netlists and testbenches; returns one entry per case:
    `{case, mode, dspf, testbench, result}`."""
    files = []
    for rc in (False, True):
        write_text(os.path.join(out, _dspf_name("dc", rc)), verify_dspf(rc))
    write_text(os.path.join(out, _dspf_name("dev", False)), verify_dspf(False, dev=True))
    for case, rc, pulse in CASES:
        tag = f"{case}_rcc"
        dev = case == "dev"
        text = rail_netlist(rc, pulse, dev)
        title = f"{case} {'devices' if dev else 'loads'}, rcc"
        if sim == "spectre":
            tb = os.path.join(out, f"tb_{tag}.scs")
            write_text(tb, spectre_testbench(text, title))
        else:
            net = f"{tag}.sp"
            write_text(os.path.join(out, net), text)
            tb = os.path.join(out, f"tb_{tag}.{'sp' if sim == 'hspice' else 'cir'}")
            write_text(tb, spice_testbench(sim, net, title))
        res = os.path.join(out, "results", f"{tag}{'.tr0' if sim == 'hspice' else '.raw'}")
        files.append({"case": case, "mode": "rcc", "testbench": tb, "result": res,
                      "dspf": os.path.join(out, _dspf_name(case, rc))})
    return files


def _stderr(line):
    print(line, file=sys.stderr)


class _Sink:
    """An `output.Out` that keeps the document of an in-process command."""
    json = False
    format = "text"

    def __init__(self):
        self.emitted = None

    def emit(self, doc):
        self.emitted = doc
        return doc

    def fmt_args(self):
        return []


def _simulate(sim, f, run, timeout, extra=None) -> tuple:
    """`emir simulate` on one case, in process: `(document, log text, argv)`."""
    from .opensim import cmd_simulate
    argv = [f["testbench"], "--sim", sim, "--dspf", f["dspf"],
            "--out", f["result"], "--prefix", "XI0.", "--timeout", str(timeout)]
    if extra:
        argv += ["--sim-args", extra]
    if run:
        argv.append("--run")
    sink = _Sink()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_simulate(argv, sink)
    return sink.emitted, buf.getvalue(), argv


def _analyse(f) -> dict:
    """`emir run` on one case's result, in process: the run summary."""
    from .dspf import analyse, build_parser
    from .selftest import sample_paths
    from .summary import summary_document
    argv = [f["dspf"], "--process", sample_paths()[1], "--cc-sim", f["result"],
            "--period", repr(PERIOD), "--net", "VDD"]
    a = build_parser().parse_args(argv)
    a._verifying = True
    a.argv, a.command = argv, "emir run " + shlex.join(argv)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), collecting(propagate=False):
        res = analyse(a)
    return {"summary": summary_document(res), "solved": res.solved}


def _check(checks, name, ok, expected=None, got=None, tolerance=None, detail=None,
           kind=None):
    """One check; `kind` is the kind of current it proves (`KINDS`), for
    the checks of a current read the way a run reads it."""
    c = {"name": name, "ok": bool(ok), "expected": expected, "got": got,
         "tolerance": tolerance, "detail": detail}
    if kind:
        c["kind"] = kind
    checks.append(c)
    return ok


def covers_of(checks: list) -> list:
    """The kinds a verification's checks cover: every kind with at least one
    check, all of whose checks passed."""
    seen = {c["kind"] for c in checks if c.get("kind")}
    failed = {c["kind"] for c in checks if c.get("kind") and not c["ok"]}
    return sorted(seen - failed)


def _rel(a, b) -> float:
    if a is None or b is None:
        return float("inf")
    return abs(a - b) / max(abs(b), 1e-30)


def _close(got, want, rel_tol) -> bool:
    """`got` within `rel_tol` of `want`; a zero answer within TOL["zero_A"]."""
    if got is None or got != got:                  # None or NaN
        return False
    if want == 0.0:
        return abs(got) <= TOL["zero_A"]
    return _rel(got, want) <= rel_tol


def _window_stats(w, name) -> dict | None:
    """avg / rms / peak of one saved current over the last whole periods
    (the name resolved as a run resolves it: `XI0.Rm1:1` answers `Rm1:1`)."""
    import numpy as np
    hit = w.match([name]).get(name)
    if hit is None:
        return None
    t = np.asarray(w.x, dtype=float)
    y = np.real(np.asarray(w.data[hit])).astype(float)
    t1 = t[-1]
    n = int(np.floor((t1 - t[0]) / PERIOD + 1e-6))
    t0 = t1 - n * PERIOD
    keep = t >= t0 - 1e-15
    tt, yy = t[keep], y[keep]
    span = tt[-1] - tt[0]
    if span <= 0:
        return {"avg": float(yy[-1]), "rms": float(abs(yy[-1])), "peak": float(abs(yy[-1]))}
    avg = float(np.trapezoid(yy, tt) / span) if hasattr(np, "trapezoid") else \
        float(np.trapz(yy, tt) / span)
    sq = float(np.trapezoid(yy * yy, tt) / span) if hasattr(np, "trapezoid") else \
        float(np.trapz(yy * yy, tt) / span)
    return {"avg": avg, "rms": sq ** 0.5, "peak": float(np.max(np.abs(yy)))}


def _item_ratios(summary, name) -> dict:
    for n in summary.get("nets") or []:
        for it in n.get("worst_items") or []:
            if it.get("name") == name:
                return it.get("ratios") or {}
    return {}


def evaluate(sim: str, files: list, docs: dict) -> list:
    """Read every case's result back, run rcc on it, and compare with the
    analytic answers (module doc)."""
    from . import waves
    checks: list = []
    for f in files:
        tag = f"{f['case']}_rcc"
        doc = docs.get(tag) or {}
        try:
            with collecting(propagate=False):
                w = waves.read(f["result"])
        except (EmirError, OSError) as e:
            _check(checks, f"{tag}: the result is read", False, detail=str(e))
            continue
        _check(checks, f"{tag}: the result is read", True, detail=w.summary())
        _check(checks, f"{tag}: written as {FORMAT[sim]}", w.meta.get("format") == FORMAT[sim],
               FORMAT[sim], w.meta.get("format"))
        named = w.meta.get("simulator")
        _check(checks, f"{tag}: the result (or its sidecar) names {sim}", named == sim, sim,
               named)
        missing = doc.get("missing") or []
        _check(checks, f"{tag}: every saved current is in the result", not missing, [],
               missing)
        want_b = dict(DC["branch_A"])
        if f["case"] == "dev":
            want_b.update({k: 0.0 for k in DEV_STUBS})
        for r, want in want_b.items():
            # the pulsed loads average 1 mA each too: the same averages
            st = _window_stats(w, f"{r}:1")
            got = st and st["avg"]
            _check(checks, f"{tag}: {r} average current (positive from the feed)",
                   st is not None and _close(got, want, TOL["current"]), want, got,
                   TOL["current"] if want else TOL["zero_A"], kind="branch")
        try:
            s = _analyse(f)["summary"]
        except EmirError as e:
            _check(checks, f"{tag}: emir run", False, detail=f"{e.code}: {e.message}")
            continue
        net = (s.get("nets") or [{}])[0]
        worst = (net.get("max_ratio") or {}).get("all") if isinstance(
            net.get("max_ratio"), dict) else net.get("max_ratio")
        _check(checks, f"{tag}: worst ratio (avg, Rm1..Rm3)",
               _rel(worst, DC["worst_ratio"]) <= TOL["ratio"], DC["worst_ratio"], worst,
               TOL["ratio"])
        _check(checks, f"{tag}: IR drop", _rel(net.get("ir_drop_mV"), DC["ir_drop_mV"])
               <= TOL["ir"], DC["ir_drop_mV"], net.get("ir_drop_mV"), TOL["ir"])
        _check(checks, f"{tag}: verdict", s.get("verdict") == "FAIL", "FAIL", s.get("verdict"))
        if f["case"] == "pulse":
            for item, want in (("Rm1", PULSE["Rm1"]), ("Rc3", PULSE["Rc3"])):
                got = _item_ratios(s, item)
                for metric, v in want.items():
                    tol = TOL["rms"] if metric == "rms" else TOL["ratio"]
                    _check(checks, f"{tag}: {item} {metric} ratio",
                           _rel(got.get(metric), v) <= tol, v, got.get(metric), tol)
    return checks


def cmd_verify_sim(argv, out) -> int:
    """`emir verify-sim`."""
    ap = parser("emir verify-sim", description=(
        "Prove on this machine that a simulator's route into EMIR gives the right numbers: "
        "simulate small testbenches with hand-checkable answers (the sample rail with ideal "
        "current-source loads, DC and pulsed, R and RC; and with real devices on it -- a "
        "diode-connected PMOS and NMOS, a resistor, a subcircuit-wrapped NMOS), every "
        "branch current saved, through the same route a run takes (save lines, names "
        "sidecar, reader), run rcc on them and compare with the analytic answers; and "
        "record the outcome in the EMIR state directory ($EMIR_STATE_DIR, else "
        "~/.emir/verified.json). Runs then count a result as verified only when the latest "
        "record for its simulator, version, format and --sim-args passed with this emir "
        "minor version. Spectre and HSPICE need a licence: without --run the decks are "
        "written and the commands printed, nothing runs. Screening, not sign-off."))
    ap.add_argument("--sim", required=True, choices=SIMS, help="the simulator to verify")
    ap.add_argument("--run", action="store_true",
                    help="spectre, hspice (licensed): run them (ask the person first); "
                         "xyce and ngspice always run")
    ap.add_argument("--out", default=None, metavar="DIR",
                    help="where the testbenches and results go (default "
                         "emir_out/verify-sim/<sim>)")
    ap.add_argument("--timeout", type=float, default=600.0, metavar="S",
                    help="stop each simulation after this many seconds (default 600)")
    ap.add_argument("--record-host", action="store_true",
                    help="also keep this machine's host name in the record (left out "
                         "unless asked); a record with a host counts only on that host")
    ap.add_argument("--sim-args", default=None, metavar="ARGS",
                    help="extra simulator arguments, as your runs use them (Spectre APS "
                         "'+aps', Spectre X '+preset=cx +postlpreset=off' ...): the "
                         "verification then covers that configuration only (runs simulated "
                         "with the same --sim-args); kept in the record")
    a = ap.parse_args(argv)
    from .opensim import sim_args
    sim_args(a.sim_args, "verify-sim")
    sim = a.sim
    run = sim not in LICENSED or a.run
    if run:
        _can_keep_record()
    od = os.path.abspath(a.out or os.path.join("emir_out", "verify-sim", sim))
    files = build(sim, od)
    docs, logs, plans = {}, [], []
    from .opensim import sim_exe, sim_version, version_command
    exe = version = None
    if run:
        exe = sim_exe(sim)
        version = sim_version(sim, exe)
    for f in files:
        tag = f"{f['case']}_{f['mode']}"
        doc, text, sargv = _simulate(sim, f, run, a.timeout, a.sim_args)
        docs[tag] = doc
        logs.append(text)
        plans.append({"case": f["case"], "mode": f["mode"], "testbench": f["testbench"],
                      "deck": doc.get("deck"), "command": doc.get("command"),
                      "cwd": doc.get("cwd"), "result": doc.get("result"),
                      "emir_simulate": command_line("simulate", sargv)})
    if not run:
        from . import site
        p, _src = site.tool(sim)
        cmd = version_command(sim, p or sim)
        notice("EMIR-I661", f"{sim} needs a licence: the {len(plans)} decks are written under "
               f"{od} and nothing was run. Ask the person; then re-run with --run", _stderr)
        print(f"[emir] verify-sim {sim}: plan only (licensed); the decks are in {od}")
        for pl in plans:
            print(f"  {pl['case']:6s}: {shlex.join(pl['command'] or [])}  (in {pl['cwd']})")
        out.emit(document(
            SCHEMA, simulator=sim, ran=False, passed=None, version=None,
            sim_args=a.sim_args,
            version_command=cmd, format=FORMAT[sim], date=None, host=None, out_dir=od,
            covers=[], cases=plans, checks=[], record=None,
            records_file=records_path(), code=None,
            next_steps=[{"why": f"ask the person first ({sim} is licensed); then run the "
                                f"verification: {len(plans)} short simulations of the "
                                f"testbenches",
                         "command": command_line("verify-sim",
                                                 strip_options(argv, {"--format": 1}),
                                                 "--run", out.fmt_args())}]))
        return 0
    checks = []
    _check(checks, f"{sim} printed its version ({shlex.join(version_command(sim, exe))})",
           bool(version_key(version)), "a version", version,
           detail=None if version_key(version) else
           "a record is per version: without one no run could be matched to it")
    checks += evaluate(sim, files, docs)
    passed = all(c["ok"] for c in checks) and bool(checks)
    rec = {"schema": RECORD_SCHEMA, "simulator": sim, "version": version,
           "version_command": version_command(sim, exe), "format": FORMAT[sim],
           "sim_args": args_key(a.sim_args) or None, "emir_version": _ver(),
           "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "host": this_host() if a.record_host else None,
           "passed": passed, "covers": covers_of(checks), "checks": checks}
    kept = save_record(rec)
    n_bad = sum(1 for c in checks if not c["ok"])
    for c in checks:
        print(f"  {'ok  ' if c['ok'] else 'FAIL'} {c['name']}"
              + ("" if c["ok"] or c["got"] is None else
                 f": got {c['got']!r}, expected {c['expected']!r}"))
    print(f"verify-sim {sim} ({version or 'version unknown'}): "
          f"{'PASS' if passed else 'FAIL'} -- {len(checks) - n_bad} of {len(checks)} checks"
          + (f"; record kept in {kept}" if kept else ""))
    if passed:
        print(f"  covers: {', '.join(KINDS[k] for k in rec['covers'])}")
    steps = []
    if not passed:
        steps.append({"why": "keep the testbenches and results, and read the failing "
                             "checks; report them with the simulator's version",
                      "command": f"ls {shlex.quote(od)}"})
    doc = document(SCHEMA, simulator=sim, ran=True, passed=passed, version=version,
                   sim_args=rec["sim_args"],
                   version_command=rec["version_command"], format=FORMAT[sim],
                   date=rec["date"], host=rec["host"], out_dir=od, covers=rec["covers"],
                   cases=plans, checks=checks, record=rec if kept else None,
                   records_file=kept or records_path(),
                   code=None if passed else "EMIR-E663", next_steps=steps)
    out.emit(doc)
    from .errors import EXIT_INTERNAL
    return 0 if passed else EXIT_INTERNAL


def _can_keep_record() -> None:
    """Before any simulation: refuse a verification whose record could not be
    kept -- the records file this user may not read (EMIR-E102), or a state
    directory it may not write (EMIR-E105) -- rather than simulate (a
    licensed run, perhaps) for a record no run would find (EMIR-W467)."""
    path = records_path()
    d = os.path.dirname(path)
    try:
        os.makedirs(d, exist_ok=True)
        why = None if os.access(d, os.W_OK) else "this user may not write it"
    except OSError as e:
        why = f"it cannot be made ({e.strerror or e})"
    if why:
        raise EmirError("EMIR-E105", f"{d}, the EMIR state directory: {why}, so a "
                        f"verification here could not keep its record (EMIR-W467); set "
                        f"EMIR_STATE_DIR to a directory of this user's own", path=d)
    prob = records_unreadable(path)
    if prob:
        raise EmirError("EMIR-E102", f"{path} {prob}: this user may not read the verification "
                        f"records, so a verification here could not keep its record "
                        f"(EMIR-W467) and no run would count it; give this user read access "
                        f"to it, or set EMIR_STATE_DIR to a directory of this user's own",
                        path=path)


def _ver() -> str:
    from . import __version__
    return __version__
