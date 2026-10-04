"""Packaging, launcher and public-repo hygiene: one test per change made when
the 1.0.0 release became the public 1.1.0 tree (see CHANGELOG.md)."""
import json
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)

import emir  # noqa: E402
from emir.check import Report, check_process  # noqa: E402
from emir.selftest import SAMPLE_CURRENTS, sample_dir, sample_paths  # noqa: E402

# ------------------------------------------------------------------ sample


def test_sample_is_package_data():
    d = sample_dir()
    assert os.path.normpath(d).endswith(os.path.join("emir", "data", "sample"))
    dspf, proc = sample_paths()
    assert os.path.isfile(dspf) and os.path.isfile(proc)


def test_sample_process_is_self_consistent_and_synthetic():
    """The sample warns only that it is illustrative: its Cont via names a
    layer that exists (it used to name a missing Poly), and its GDS numbers are
    obviously synthetic (100/0 and up)."""
    import yaml
    _, proc = sample_paths()
    rep = Report()
    assert check_process(proc, rep) is not None
    warns = [f.message for f in rep.findings if f.level == "WARN"]
    assert len(warns) == 1 and "ILLUSTRATIVE" in warns[0], warns
    data = yaml.safe_load(open(proc))
    for sect in ("layers", "vias"):
        for name, d in data[sect].items():
            assert d["gds"][0] >= 100, (name, d["gds"])
    for v in data["vias"].values():
        assert v["below"] in data["layers"] and v["above"] in data["layers"]


def test_version_is_dev():
    assert emir.__version__ == "1.1.0.dev0"


# ------------------------------------------------------------------ launcher


def _env():
    return {**os.environ, "EMIR_PYTHON": sys.executable}


def test_bin_emir_through_symlinks(tmp_path):
    """bin/emir resolves its own path, so a symlink (or a chain of them) on
    $PATH still finds the package next to the real file."""
    a = tmp_path / "a"
    a.mkdir()
    (a / "emir").symlink_to(os.path.join(ROOT, "bin", "emir"))
    (tmp_path / "emir2").symlink_to(os.path.join("a", "emir"))      # relative link
    for link in (a / "emir", tmp_path / "emir2"):
        p = subprocess.run([str(link), "--version"], cwd=str(tmp_path), env=_env(),
                           capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        assert p.stdout.strip() == f"emir {emir.__version__}"


def test_selftest_script_through_symlink(tmp_path):
    (tmp_path / "st").symlink_to(os.path.join(ROOT, "scripts", "selftest.sh"))
    p = subprocess.run([str(tmp_path / "st")], cwd="/", env=_env(),
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "SELFTEST PASSED" in p.stdout


# ------------------------------------------------------------------ gui-install


def test_gui_install_default_dir_in_a_clone():
    from emir.skillgui import default_dir
    assert default_dir() == os.path.join(ROOT, "skill")


def test_gui_install_default_dir_when_pip_installed(monkeypatch, tmp_path):
    """No bin/emir next to the package (a pip install): never site-packages."""
    from emir import skillgui
    real = os.path.isfile
    monkeypatch.setattr(skillgui.os.path, "isfile",
                        lambda p: False if p.endswith(os.path.join("bin", "emir")) else real(p))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert skillgui.default_dir() == os.path.join(str(tmp_path), ".emir", "skill")


def test_gui_install_header_names_the_real_file(tmp_path):
    """The site stub (the file .cdsinit loads) names itself in its header;
    emir_main.il holds no path at all."""
    from emir.skillgui import install
    files = install(str(tmp_path / "sk"))
    head = open(files["site"]).read(600)
    assert f'load("{files["site"]}")' in head and "@MAIN@" not in head
    assert str(tmp_path) not in open(files["main"]).read()


# ------------------------------------------------------------------ viewer


def _viewer_run(out, monkeypatch, site_data):
    from emir import site
    from emir.dspf import build_parser, run
    monkeypatch.setattr(site, "_cache", dict(site_data))
    dspf, proc = sample_paths()
    a = build_parser().parse_args([dspf, "--net", "VDD", "--process", proc,
                                   "--currents", SAMPLE_CURRENTS,
                                   "--viewer", str(out), "--lib", "L"])
    run(a)
    return (out / "emir_data.il").read_text()


def test_mark_layer_defaults_to_the_entry_layer(tmp_path, monkeypatch):
    """No PDK layer is assumed: with no `mark_layer` in the site file the data
    leaves it empty and the Explorer paints on the current entry layer."""
    data = _viewer_run(tmp_path / "v", monkeypatch, {})
    assert 'emirMarkLayer = ""' in data
    data = _viewer_run(tmp_path / "w", monkeypatch, {"mark_layer": ["M1", "drawing"]})
    assert 'emirMarkLayer = "M1"' in data and 'emirMarkPurpose = "drawing"' in data


def test_explorer_falls_back_to_entry_layer_for_unset_mark_layer():
    from emir.selftest import _parens_balanced
    from emir.skillapp import APP
    assert _parens_balanced(APP)
    assert 'want = and(stringp(emirMarkLayer) emirMarkLayer != "")' in APP
    assert "leGetEntryLayer()" in APP


def test_export_lib_default_is_none():
    import inspect

    from emir.layoutview import export
    assert inspect.signature(export).parameters["lib"].default is None


# ------------------------------------------------------------------ engine


# ------------------------------------------------------------------ import-ict

#: an invented ICT: the long-wire branch is `L > 20` and comes SECOND, after a
#: more generous short-wire branch
FAKE_ICT = """process "DEMO" {
    temp_reference 50.0
    em_tref 100.0
}
conductor "metal1_conn" {
    temp_tc1 0.003
    em_jmax_dc_avg EQU 1 * 6.0 * ( w - 0.01 ) L <= 20
    em_jmax_dc_avg EQU 1 * 2.0 * ( w - 0.01 ) jmax_factor 50 4.0 100 1.0 L > 20
}
"""


def test_import_ict_prefers_any_long_wire_branch(tmp_path):
    from emir.pdk_import import parse_ict
    p = tmp_path / "demo.ict"
    p.write_text(FAKE_ICT)
    ict = parse_ict(str(p))
    assert ict["conductors"]["metal1_conn"]["jmax_avg"] == pytest.approx(2.0)
    assert ict["jmax_factor"] == {50.0: 4.0, 100.0: 1.0}


# ------------------------------------------------------------------ build_bundle


def _bundle(*args):
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "EMIR_ALLOW_LOCAL_BUNDLE")}
    return subprocess.run(["sh", os.path.join(ROOT, "scripts", "build_bundle.sh"), *args],
                          cwd=ROOT, env=env, capture_output=True, text=True)


def test_build_bundle_refuses_outside_ci():
    had_build = os.path.exists(os.path.join(ROOT, "build"))
    p = _bundle()
    assert p.returncode == 2 and "meant for CI" in p.stderr
    assert os.path.exists(os.path.join(ROOT, "build")) == had_build


def test_build_bundle_needs_a_checksum_for_another_python():
    p = _bundle("--pyver", "3.12.0")
    assert p.returncode == 2 and "--sha256" in p.stderr


def test_build_bundle_is_hardened():
    lines = open(os.path.join(ROOT, "scripts", "build_bundle.sh")).read().splitlines()
    code = "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))
    assert "--user" not in code and "||" not in code       # no user installs, no fallbacks
    assert "sha256sum -c" in code and "--owner=0 --group=0 --numeric-owner" in code
    assert "PYTHONDONTWRITEBYTECODE=1" in code and "PIP_USER=0" in code
    # relocatable: no build path in pip's records or console-script shebangs,
    # and the build fails if any text file in the staged tree still names it
    assert "-name direct_url.json" in code and "-delete" in code
    assert "relocate_launchers.sh" in code and 'grep -rlI -- "$STAGE" "$STAGE"' in code


#: a build path longer than a kernel accepts in a shebang (~127 characters):
#: pip then writes its three-line sh trampoline with the path on line 2
LONG_BUILD = "/b" + "/x" * 80


@pytest.mark.parametrize("build", ["/b/short", LONG_BUILD])
def test_relocate_launchers_handles_both_pip_forms(tmp_path, build):
    """Step 6b of the bundle rewrote only a launcher whose FIRST line named the
    build path; with a long build path pip puts it on line 2, and the build
    then failed closed on its own leak check (seven launchers)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    py = f"{build}/python/bin/python3"
    body = "# -*- coding: utf-8 -*-\nimport sys\nprint('launcher body')\n"
    short = bindir / "emir"
    short.write_text(f"#!{py}\n{body}")
    longf = bindir / "f2py"
    longf.write_text("#!/bin/sh\n" + "'" * 3 + f"exec' \"{py}\" \"$0\" \"$@\"\n' " + "'" * 3
                     + f"\n{body}")
    other = bindir / "tool"
    other.write_text("#!/bin/sh\necho untouched\n")
    (bindir / "python3").write_text("#!/bin/sh\necho \"ran $1\"\n")
    for f in bindir.iterdir():
        f.chmod(0o755)
    (bindir / "link").symlink_to("emir")
    p = subprocess.run(["sh", os.path.join(ROOT, "scripts", "relocate_launchers.sh"),
                        str(bindir), build], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    for f in (short, longf):
        text = f.read_text()
        assert build not in text, text
        lines = text.splitlines()
        assert lines[0] == "#!/bin/sh" and lines[1].startswith("'" * 3 + "exec'")
        assert lines[2] == "' " + "'" * 3 and text.endswith(body)
        assert os.access(f, os.X_OK)
        # the trampoline runs the python3 beside it, with the launcher as argv[1]
        r = subprocess.run([str(f)], capture_output=True, text=True)
        assert r.stdout.strip() == f"ran {f}", r.stdout + r.stderr
        # ... also when called through a chain of symlinks from elsewhere (it
        # once looked for python3 beside the link: rc 127), by an absolute
        # and by a relative path
        away = tmp_path / f"away-{f.name}"
        away.mkdir()
        (away / "hop").symlink_to(os.path.relpath(f, away))
        (away / "link").symlink_to("hop")
        r = subprocess.run([str(away / "link")], capture_output=True, text=True)
        assert r.returncode == 0 and r.stdout.strip() == f"ran {away / 'link'}", r.stderr
        r = subprocess.run(["./link"], cwd=away, capture_output=True, text=True)
        assert r.returncode == 0 and r.stdout.strip() == "ran ./link", r.stderr
        # and Python reads lines 2-3 as one string literal: the body still runs
        compile(text, str(f), "exec")
    assert other.read_text() == "#!/bin/sh\necho untouched\n"
    assert (bindir / "link").is_symlink()


def test_relocate_launchers_needs_its_arguments():
    p = subprocess.run(["sh", os.path.join(ROOT, "scripts", "relocate_launchers.sh")],
                       capture_output=True, text=True)
    assert p.returncode == 2 and "usage" in p.stderr


def test_third_party_notices_name_everything_the_bundle_ships():
    notices = open(os.path.join(ROOT, "THIRD_PARTY_NOTICES.md"), encoding="utf-8").read()
    script = open(os.path.join(ROOT, "scripts", "build_bundle.sh"), encoding="utf-8").read()
    deps = re.search(r'^DEPS="([^"]+)"', script, re.M).group(1).split()
    names = [re.split(r"[<>=!]", d)[0] for d in deps]
    # matplotlib's own dependencies come along with it (pip download resolves them)
    names += ["contourpy", "cycler", "fonttools", "kiwisolver", "pillow", "packaging",
              "pyparsing", "python-dateutil", "six"]
    names += ["CPython", "python-build-standalone", "Tcl/Tk", "pip", "setuptools"]
    low = notices.lower()
    for n in names:
        assert n.lower().replace("pyyaml", "pyyaml") in low, n
    assert "THIRD_PARTY_NOTICES.md" in script            # copied into the bundle
    assert "THIRD_PARTY_NOTICES.md" in open(os.path.join(ROOT, "MANIFEST.in")).read()


def test_the_bundle_ships_its_licences_pdks_and_examples():
    """The offline bundle carried no licence text for what the standalone
    Python links (OpenSSL, ncurses, libffi, Tcl ...), no pdks/ (the TEMPLATE
    the onboarding page copies) and no examples/; a missing LICENSE was
    skipped silently."""
    script = open(os.path.join(ROOT, "scripts", "build_bundle.sh"), encoding="utf-8").read()
    assert "pgo+lto-full.tar.zst" in script and "PBS_FULL_SHA256=" in script
    assert re.search(r'sha256sum -c', script.split("== 2b.")[1].split("== 3.")[0])
    assert "python/licenses" in script and "licenses/python-build-standalone" in script
    for need in ("openssl", "ncurses", "libffi", "tcl", "bzip2", "liblzma", "libedit",
                 "zlib", "sqlite"):
        assert need in script, need
    assert "GCC-RUNTIME.txt" in script and "libquadmath" in script
    assert 'cp -r "$HERE/pdks" "$STAGE/pdks"' in script
    assert 'cp -r "$HERE/examples" "$STAGE/examples"' in script
    assert "if [ -f \"$HERE/$f\" ]" not in script       # no silent skip of LICENSE
    assert "bin/emir check --process pdks/TEMPLATE.yaml" in script
    notices = open(os.path.join(ROOT, "THIRD_PARTY_NOTICES.md"), encoding="utf-8").read()
    assert "licenses/python-build-standalone" in notices and "GCC-RUNTIME.txt" in notices


# ------------------------------------------------------------------ check --json-out


def test_check_json_out_creates_dirs(tmp_path):
    dspf, proc = sample_paths()
    out = tmp_path / "a" / "b" / "check.json"
    p = subprocess.run([sys.executable, "-m", "emir", "check", dspf, "--process", proc,
                        "--net", "VDD", "--currents", SAMPLE_CURRENTS, "--json-out", str(out)],
                       cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.load(open(out))["passed"] is True


# ------------------------------------------------------------------ install hints


def test_the_extras_of_the_hints_are_pyprojects():
    # tomllib is Python 3.11+; on 3.10 tomli reads the same, when installed
    try:
        import tomllib
    except ModuleNotFoundError:
        tomllib = pytest.importorskip("tomli", reason="Python 3.10 without tomli")
    from emir import repo
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        extras = tomllib.load(f)["project"]["optional-dependencies"]
    want = {k: v for k, v in extras.items() if k != "docs"}
    assert repo.EXTRAS == want


def test_no_hint_installs_emir_from_pypi():
    """`pip install 'emir[X]'` fails (emir is not on PyPI) or, once someone
    registers the name, installs an unrelated package: no user-facing text
    says it."""
    import re
    bad = re.compile(r"""pip install ["']?emir\[""")
    hits = []
    for top in ("emir", "docs", "agent", "examples", "pdks", "scripts", "bin"):
        for d, _dirs, names in os.walk(os.path.join(ROOT, top)):
            if "__pycache__" in d or "/out" in d or "emir_out" in d:
                continue
            for n in names:
                if n.endswith((".py", ".md", ".sh", ".yaml", ".json", ".toml", ".il", "")):
                    p = os.path.join(d, n)
                    try:
                        text = open(p, encoding="utf-8").read()
                    except (UnicodeDecodeError, OSError):
                        continue
                    if bad.search(text):
                        hits.append(os.path.relpath(p, ROOT))
    for f in ("README.md", "AGENTS.md", "CONTRIBUTING.md"):
        if bad.search(open(os.path.join(ROOT, f), encoding="utf-8").read()):
            hits.append(f)
    assert not hits, hits


def test_no_skip_reason_installs_emir_from_pypi():
    """`pytest -rs` prints the skip reasons to contributors: they name the
    install hint of the install (`emir.repo.install_hint`), never pip's
    `emir[X]`."""
    import glob
    import re
    bad = re.compile(r"""pip install ["']?emir\[""")
    hits = []
    for p in sorted(glob.glob(os.path.join(ROOT, "tests", "*.py"))):
        text = open(p, encoding="utf-8").read()
        for m in re.finditer(r"reason=|pytest\.skip\(", text):
            if bad.search(text[m.end():m.end() + 200]):
                hits.append(f"{os.path.basename(p)}:{text[:m.start()].count(chr(10)) + 1}")
    assert not hits, hits


def test_the_hint_follows_the_install(monkeypatch, tmp_path):
    from emir import repo
    assert repo.install_hint("all").startswith("pip install -e ")       # this clone
    monkeypatch.setattr(repo, "clone_root", lambda: None)
    monkeypatch.setattr(repo, "bundle_root", lambda: None)
    assert repo.install_hint("all") == ("pip install 'matplotlib>=3.6' 'klayout>=0.29' "
                                        "'mcp>=1.19'")
    monkeypatch.setattr(repo, "bundle_root", lambda: str(tmp_path))
    assert repo.install_hint("mcp").endswith("python3 -m pip install 'mcp>=1.19'")


def test_a_missing_core_package_is_called_required(monkeypatch):
    from emir import cli
    err = cli.coded(ImportError("No module named 'yaml'", name="yaml"))
    assert err.code == "EMIR-E603" and "required" in err.message and "pyyaml" in err.message
    assert "optional" not in err.message
    err = cli.coded(ImportError("No module named 'klayout'", name="klayout"))
    assert "optional" in err.message and "pip install 'emir[" not in err.message


def test_the_launcher_exits_3_when_no_python_fits(tmp_path):
    """No interpreter with the dependencies is a missing tool: exit 3 (it
    was 2, which the contract keeps for bad input)."""
    import shutil
    fake = tmp_path / "python3"
    fake.write_text("#!/bin/sh\nexit 1\n")
    fake.chmod(0o755)
    for tool in ("dirname", "readlink"):          # all the launcher needs from PATH
        (tmp_path / tool).symlink_to(shutil.which(tool))
    env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    p = subprocess.run(["/bin/sh", os.path.join(ROOT, "bin", "emir"), "info"], env=env,
                       capture_output=True, text=True)
    assert p.returncode == 3 and "EMIR-E603" in p.stderr, p.stderr


@pytest.mark.parametrize("value", ["/nonexistent/python", "no-such-python-here", "."])
def test_the_launcher_exits_3_when_emir_python_is_not_a_python(value):
    """EMIR_PYTHON naming a missing interpreter (or a directory) is a missing
    tool, exit 3 with EMIR-E603: not the shell's exit 127."""
    env = {"PATH": "/usr/bin:/bin", "EMIR_PYTHON": value}
    p = subprocess.run(["/bin/sh", os.path.join(ROOT, "bin", "emir"), "--version"], env=env,
                       capture_output=True, text=True)
    assert p.returncode == 3, (p.returncode, p.stderr)
    assert "EMIR-E603" in p.stderr and f"EMIR_PYTHON={value}" in p.stderr


def test_the_launcher_takes_emir_python_by_name_or_path(tmp_path):
    for value in (sys.executable, os.path.basename(sys.executable)):
        env = {"PATH": f"{os.path.dirname(sys.executable)}:/usr/bin:/bin",
               "EMIR_PYTHON": value, "HOME": str(tmp_path)}
        p = subprocess.run(["/bin/sh", os.path.join(ROOT, "bin", "emir"), "--version"],
                           env=env, capture_output=True, text=True)
        assert p.returncode == 0 and p.stdout.startswith("emir "), (value, p.stderr)


# ------------------------------------------------------------------ the PyPI description


def test_the_long_description_holds_no_relative_link():
    """PyPI and `pip show -v` show the long description with no repository
    around it: README.md's relative links (`docs/...`, `docs/images/hero.png`)
    would break there. The package's readme is README.pypi.md, generated from
    README.md with every link absolute, and it is current."""
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        assert re.search(r'^readme = \{ file = "README\.pypi\.md"', f.read(), re.M)
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "gen_pypi_readme.py"),
                        "--check"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr or p.stdout
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import gen_pypi_readme as g
    with open(os.path.join(ROOT, "README.pypi.md"), encoding="utf-8") as f:
        text = f.read()
    assert g.relative_links(text) == []
    # PyPI drops a <picture>'s <source> and shows its <img> on a light page:
    # the banner there is the light one, as an absolute raw URL
    raw = "https://raw.githubusercontent.com/skycadeda/emir/main/docs/images/"
    assert f'src="{raw}hero_light.png"' in text and f"{raw}hero.png" not in text
    assert "<picture>" not in text and "<source" not in text
    assert "https://github.com/skycadeda/emir/blob/main/AGENTS.md" in text
    # the converter itself: pages, directories, anchors, images; absolute ones kept
    assert g.absolute("docs/x.md#a") == "https://github.com/skycadeda/emir/blob/main/docs/x.md#a"
    assert g.absolute("examples/02_xyce/") == \
        "https://github.com/skycadeda/emir/tree/main/examples/02_xyce/"
    assert g.absolute("#top") == "#top" and g.absolute("https://x.org/a") == "https://x.org/a"


def test_the_wheel_carries_every_notice_file_its_notice_names():
    """NOTICE (in the wheel's licence files) sends the reader to
    THIRD_PARTY_NOTICES.md for the derived code's credits: a pip install
    carried NOTICE without it."""
    text = open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read()
    files = re.findall(r'"([^"]+)"', re.search(r"^license-files = \[(.*)\]$", text,
                                                 re.M).group(1))
    notice = open(os.path.join(ROOT, "NOTICE"), encoding="utf-8").read()
    named = set(re.findall(r"\b[A-Z_]+\.md\b", notice))
    assert named == {"THIRD_PARTY_NOTICES.md"}
    assert {"LICENSE", "NOTICE"} | named <= set(files)
    for f in files:
        assert os.path.isfile(os.path.join(ROOT, f)), f
