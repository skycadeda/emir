"""A reader that leaves early never costs a command its result.

Agents read commands through pipes that close early: `emir run ... --format
json 2>&1 | head -c 300`. The next log line then meets a broken pipe. The
command must still finish, write its files (--summary-out, --out, -o) and
exit with its own status; only the lines nobody reads are lost. Covered for
run, the magic route and simulate, in two ways:

* in a child process whose stdout and stderr are a pipe with no reader at all
  (every write fails: the strongest case, and the interpreter's final flush
  must not turn the exit status into 120);
* in-process, with a stream that breaks after a few hundred characters, so
  the break comes in the middle of the analysis, as with `| head -c 300`.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys

import pytest
from _helpers import CUR, DSPF, PROC, ROOT, TINY, fake_magic, write

from emir import cli, site
from emir.output import PipeSafe, pipe_safe
from emir.repo import install_hint

SKY = os.path.join(ROOT, "pdks", "sky130.yaml")
EXAMPLES = os.path.join(ROOT, "examples")
HAVE_KLAYOUT = importlib.util.find_spec("klayout") is not None
SIM = next((s for s in ("ngspice", "xyce") if site.tool(s)[0] is not None), None)

needs_examples = pytest.mark.skipif(not os.path.isdir(EXAMPLES),
                                    reason="no examples/ in this tree (an sdist)")


def closed_pipe_run(args, cwd=None, stdout=None) -> int:
    """`python -m emir ARGS` with stderr (and stdout, unless given) on a pipe
    whose reader is already gone; the exit status."""
    r, w = os.pipe()
    os.close(r)
    try:
        p = subprocess.run([sys.executable, "-m", "emir", *map(str, args)],
                           stdin=subprocess.DEVNULL, stdout=w if stdout is None else stdout,
                           stderr=w, cwd=cwd or ROOT, timeout=600)
    finally:
        os.close(w)
    return p.returncode


class BreaksAfter(io.StringIO):
    """A stream whose reader leaves after `n` characters (`| head -c n`)."""

    def __init__(self, n):
        super().__init__()
        self.n = n
        self.refused = 0

    def write(self, s):
        if self.tell() + len(s) > self.n:
            self.refused += 1
            raise BrokenPipeError(32, "Broken pipe")
        return super().write(s)


def head_c(args, n=300) -> tuple:
    """`emir ARGS 2>&1 | head -c n`, in-process: the exit status and the
    shared stream."""
    s = BreaksAfter(n)
    with contextlib.redirect_stdout(s), contextlib.redirect_stderr(s):
        rc = cli.main([str(a) for a in args])
    return rc, s


def schema_of(path) -> str:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["schema"]


RUN = ["run", DSPF, "--process", PROC, "--currents", CUR]


# ------------------------------------------------------------------ the stream itself


def test_pipe_safe_swallows_the_first_broken_pipe_and_drops_the_rest():
    s = BreaksAfter(5)
    w = PipeSafe(s)
    assert w.write("abc") == 3 and not w.gone
    assert w.write("defgh") == 5 and w.gone            # swallowed, marked gone
    assert w.write("more") == 4 and s.refused == 1      # never tried again
    w.flush()
    w.writelines(["x", "y"])
    assert s.getvalue() == "abc" and s.refused == 1
    assert pipe_safe(w) is w and w.stream is s
    assert w.getvalue() == "abc"                       # everything else is the stream's


def test_main_gives_the_callers_streams_back():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        assert cli.main(["explain", "EMIR-E101"]) == 0
        assert sys.stdout is out and sys.stderr is err
    assert "EMIR-E101" in out.getvalue()


# ------------------------------------------------------------------ run


@pytest.mark.parametrize("extra, rc", [((), 0), (("--fail-on-violation",), 1)])
def test_run_mid_analysis_break_keeps_the_summary_and_the_status(tmp_path, extra, rc):
    summ = tmp_path / "o" / "summary.json"
    code, s = head_c([*RUN, "--summary-out", summ, "--format", "json", *extra])
    assert s.refused >= 1, "the stream never broke: the test proves nothing"
    assert code == rc
    doc = json.loads(summ.read_text())
    assert doc["schema"] == "emir.run/1" and doc["verdict"] == "FAIL"


@pytest.mark.parametrize("fmt", [["--format", "json"], []])
def test_run_with_no_reader_at_all(tmp_path, fmt):
    summ = tmp_path / "summary.json"
    assert closed_pipe_run([*RUN, "--summary-out", summ, *fmt]) == 0
    assert schema_of(summ) == "emir.run/1"
    assert closed_pipe_run([*RUN, "--summary-out", summ, "--fail-on-violation", *fmt]) == 1
    assert schema_of(summ) == "emir.run/1"


def test_run_stdout_document_survives_a_closed_stderr(tmp_path):
    """`2> >(head -c 10)`: the logs are lost, the document on stdout is whole."""
    summ = tmp_path / "summary.json"
    with open(tmp_path / "doc.json", "w") as f:
        assert closed_pipe_run([*RUN, "--summary-out", summ, "--format", "json"],
                               stdout=f) == 0
    doc = json.loads((tmp_path / "doc.json").read_text())
    assert doc["schema"] == "emir.run/1" and doc == json.loads(summ.read_text())


def test_nets_listing_into_a_closed_pipe_exits_0(tmp_path):
    assert closed_pipe_run(["nets", DSPF]) == 0


# ------------------------------------------------------------------ the magic route


def test_extract_with_magic_keeps_its_files(tmp_path):
    """magic's extraction finishes and writes the DSPF when the reader of its
    output is gone (a stand-in magic, tests/_helpers)."""
    if not HAVE_KLAYOUT:
        pytest.skip(f"needs the klayout extra: {install_hint('klayout')}")
    out = tmp_path / "ext"
    rc = write(tmp_path / "rc", "#\n")
    args = ["extract", "--tool", "magic", "--gds", os.path.join(TINY, "tiny.gds"),
            "--process", SKY, "--magic", fake_magic(tmp_path), "--magicrc", rc, "--out", out]
    assert closed_pipe_run([*args, "--format", "json"]) == 0
    assert (out / "tiny.dspf").is_file() and not (out / "tiny.cc.spice").exists()


# ------------------------------------------------------------------ simulate


@contextlib.contextmanager
def _chdir(path):
    """`contextlib.chdir` (Python 3.11+)."""
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)



@needs_examples
@pytest.mark.skipif(SIM is None, reason="needs ngspice or Xyce")
def test_simulate_writes_its_result(tmp_path):
    for f in ("loadrail.dspf", "models.sp", "tb_rcc.cir"):
        shutil.copy(os.path.join(EXAMPLES, "02_xyce", f), tmp_path / f)
    raw = tmp_path / "sim" / "r.raw"
    args = ["simulate", "tb_rcc.cir", "--sim", SIM, "--dspf", "loadrail.dspf",
            "--format", "json"]
    assert closed_pipe_run([*args, "--out", raw], cwd=tmp_path) == 0
    assert raw.is_file() and (tmp_path / "sim" / "r.raw.names").is_file()
    raw2 = tmp_path / "sim2" / "r.raw"
    with _chdir(tmp_path):
        code, s = head_c([*args, "--out", raw2], n=50)
    assert s.refused >= 1 and code == 0 and raw2.is_file()
