"""The CI workflows, the issue forms and the community files: what they promise
stays true as the code moves. The `.github/` checks skip in an unpacked sdist,
which does not carry that directory (scripts/check_sdist.py, NOT_SHIPPED)."""
import glob
import os
import re
import subprocess
import sys
import tarfile

import pytest
import yaml
from _helpers import ROOT

import emir

GH = os.path.join(ROOT, ".github")
WORKFLOWS = sorted(glob.glob(os.path.join(GH, "workflows", "*.yml")))
FORMS = sorted(f for f in glob.glob(os.path.join(GH, "ISSUE_TEMPLATE", "*.yml"))
               if os.path.basename(f) != "config.yml")
needs_github = pytest.mark.skipif(not os.path.isdir(GH), reason="no .github/ (an sdist)")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _steps(wf):
    return [s for job in wf["jobs"].values() for s in job.get("steps", [])]


# ------------------------------------------------------------------ community files


def test_citation_names_the_package_version_and_licence():
    cff = _load(os.path.join(ROOT, "CITATION.cff"))
    assert cff["cff-version"] == "1.2.0"
    assert cff["version"] == emir.__version__, "move CITATION.cff's version with the package"
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        lic = re.search(rb'^license = "([^"]+)"', f.read(), re.M).group(1).decode()
    assert cff["license"] == lic
    assert cff["authors"] and "EMIR" in cff["title"]


def test_community_files_are_there_and_in_the_sdist_manifest():
    names = ("CONTRIBUTING.md", "SECURITY.md", "CITATION.cff")
    manifest = _text(os.path.join(ROOT, "MANIFEST.in"))
    for n in names:
        assert os.path.isfile(os.path.join(ROOT, n)), n
        assert n in manifest, f"{n} is not in MANIFEST.in"


def test_security_policy_names_the_two_code_paths_an_input_can_reach():
    text = _text(os.path.join(ROOT, "SECURITY.md"))
    assert "Report a vulnerability" in text
    assert "emir_data.il" in text and "ICT expression" in text


def test_every_security_bullet_is_under_its_own_heading():
    """A bullet right after a paragraph joins that section on GitHub: the
    release files belong to "Also in scope", not to "Testbenches are code"."""
    text = _text(os.path.join(ROOT, "SECURITY.md"))
    sections = re.split(r"^#{2,3} ", text, flags=re.M)
    owner = {re.search(r"^- \*\*(.+?)\*\*", b, re.M).group(1): sec.split("\n", 1)[0]
             for sec in sections for b in re.findall(r"^- \*\*.+", sec, re.M)}
    assert owner["The release files"] == "Also in scope"
    assert owner["Files EMIR writes"] == "Also in scope"
    testbenches = next(s for s in sections if s.startswith("Testbenches are code"))
    assert not re.search(r"^- ", testbenches, re.M)


def test_contributing_lists_every_gate_ci_runs():
    text = _text(os.path.join(ROOT, "CONTRIBUTING.md"))
    for gate in ("pre-commit run --all-files", "scripts/leak_guard.py", "pytest",
                 "emir selftest", "emir validate", "gen_errors_md.py --check",
                 "gen_schemas.py --check", "gen_cadence_skill.py --check",
                 "git commit -s", "LIVE_TEST.md"):
        assert gate in text, gate


# ------------------------------------------------------------------ pre-commit


def test_pre_commit_runs_the_three_generator_checks():
    cfg = _load(os.path.join(ROOT, ".pre-commit-config.yaml"))
    entries = [h["entry"] for r in cfg["repos"] for h in r["hooks"] if "entry" in h]
    for script in ("gen_errors_md.py", "gen_schemas.py", "gen_cadence_skill.py"):
        assert any(script in e and "--check" in e for e in entries), script


@pytest.mark.parametrize("script", ["gen_errors_md.py", "gen_schemas.py",
                                    "gen_cadence_skill.py"])
def test_generator_checks_need_only_the_standard_library(script):
    """pre-commit runs them in an environment without numpy, scipy or PyYAML
    (`language: python`, no dependencies): `-S` leaves out site-packages."""
    p = subprocess.run([sys.executable, "-S", os.path.join(ROOT, "scripts", script), "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    assert p.returncode == 0, p.stdout + p.stderr


# ------------------------------------------------------------------ workflows


@needs_github
def test_workflows_pin_every_action_to_a_commit():
    """An action is code that runs with the job's token: a tag can be moved, a
    commit cannot. Dependabot moves the SHA and the version comment together."""
    assert {os.path.basename(f) for f in WORKFLOWS} >= {"ci.yml", "release.yml", "docs.yml"}
    for f in WORKFLOWS:
        wf = _load(f)
        assert "permissions" in wf, f"{f}: set the token's permissions at the top"
        for line in _text(f).splitlines():
            m = re.match(r"\s*(?:-\s+)?uses:\s*(\S+)(.*)$", line)
            if not m or line.lstrip().startswith("#"):
                continue
            ref = m.group(1)
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), (f, ref)
            assert re.search(r"#\s*v\d", m.group(2)), f"{f}: {ref} needs its # vX.Y.Z comment"


@needs_github
def test_ci_runs_the_gates():
    wf = _load(os.path.join(GH, "workflows", "ci.yml"))
    jobs = wf["jobs"]
    assert set(jobs) >= {"core", "full", "lowest", "wheel", "lint", "docs"}
    assert jobs["core"]["strategy"]["matrix"]["python"] == ["3.10", "3.12", "3.13"]
    runs = "\n".join(s.get("run", "") for s in _steps(wf))
    for cmd in ("pytest -m \"not example and not cadence\"", "-m \"example and not cadence\"",
                "emir selftest", "emir validate --no-skip", "--resolution lowest-direct",
                "twine check", "scripts/check_sdist.py", "pre-commit run --all-files",
                "leak_guard.py --commit \"$c\"", "mkdocs build --strict"):
        assert cmd in runs, cmd
    # selftest runs from / so the source tree cannot shadow the installed package
    selftests = [s for s in _steps(wf) if "emir selftest" in s.get("run", "")]
    assert selftests and all(s.get("working-directory") == "/" for s in selftests)
    assert "all-green" in jobs and set(jobs["all-green"]["needs"]) == set(jobs) - {"all-green"}
    # the docs jobs install the [docs] extra
    assert re.search(r'^docs\s*=\s*\[[^\]]*mkdocs', _text(os.path.join(ROOT, "pyproject.toml")),
                     re.M)


@needs_github
def test_release_checks_the_tag_and_publishes_a_draft():
    wf = _load(os.path.join(GH, "workflows", "release.yml"))
    on = wf.get("on", wf.get(True))                      # YAML 1.1 reads `on` as True
    assert on["push"]["tags"] == ["v*"]
    runs = "\n".join(s.get("run", "") for s in _steps(wf))
    for cmd in ("emir/__init__.py", "scripts/build_bundle.sh", "SHA256SUMS",
                "gh release create", "--draft", "scripts/check_sdist.py"):
        assert cmd in runs, cmd
    assert "pypi" not in wf["jobs"], "PyPI publishing stays off until the maintainer turns it on"


# ------------------------------------------------------------------ issue forms


@needs_github
@pytest.mark.parametrize("path", FORMS, ids=[os.path.basename(f) for f in FORMS])
def test_every_form_asks_for_no_foundry_data(path):
    form = _load(path)
    boxes = [b for b in form["body"] if b["type"] == "checkboxes"]
    required = [o for b in boxes for o in b["attributes"]["options"] if o.get("required")]
    assert any("foundry" in o["label"] or "licensed" in o["label"] for o in required), path


@needs_github
def test_bug_form_asks_for_the_diagnostics():
    text = _text(os.path.join(GH, "ISSUE_TEMPLATE", "bug_report.yml"))
    for ask in ("emir --version", "emir info --format json", "emir check --format json"):
        assert ask in text, ask
    assert "SECURITY.md" in text


# ------------------------------------------------------------------ the sdist check


def test_check_sdist_finds_a_missing_file(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    for rel in ("a.py", "docs/b.md", ".github/workflows/ci.yml", "sub/.gitignore"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n")
    git("add", "-A")
    sdist = tmp_path / "p-1.tar.gz"
    with tarfile.open(sdist, "w:gz") as t:
        t.add(tmp_path / "a.py", arcname="p-1/a.py")
    script = os.path.join(ROOT, "scripts", "check_sdist.py")
    p = subprocess.run([sys.executable, script, str(sdist), "--root", str(tmp_path)],
                       capture_output=True, text=True)
    assert p.returncode == 1 and "docs/b.md" in p.stdout, p.stdout
    assert ".github/workflows/ci.yml" not in p.stdout and "sub/.gitignore" not in p.stdout
    with tarfile.open(sdist, "w:gz") as t:
        for rel in ("a.py", "docs/b.md"):
            t.add(tmp_path / rel, arcname=f"p-1/{rel}")
    p = subprocess.run([sys.executable, script, str(sdist), "--root", str(tmp_path)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout


def test_the_release_notes_fit_a_github_release(tmp_path):
    """The release body is the version's CHANGELOG section, and GitHub
    refuses a body over 125,000 characters (`gh release create` fails): a
    longer section is cut at a whole entry and links to the full CHANGELOG."""
    script = os.path.join(ROOT, "scripts", "release_notes.py")
    wf = _text(os.path.join(ROOT, ".github", "workflows", "release.yml"))
    assert "scripts/release_notes.py" in wf

    def notes(text, ver="9.9.9"):
        ch = tmp_path / "CHANGELOG.md"
        ch.write_text(text, encoding="utf-8")
        p = subprocess.run([sys.executable, script, ver, "--changelog", str(ch), "--url",
                            "https://example.org/CHANGELOG.md"],
                           capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        return p.stdout

    small = "# Changelog\n\n## [9.9.9] - 2026-10-03\n\n### Added\n\n- one thing\n\n## [1.0.0]\n\n- old\n"
    out = notes(small)
    assert out.startswith("### Added\n\n- one thing") and "old" not in out
    assert "Screening, not sign-off." in out and "SHA256SUMS" in out
    # the Release page, where the downloads are, presents the project: the
    # research disclaimer is said there too (the script's copy is the package's)
    from emir import RESEARCH_DISCLAIMER
    assert RESEARCH_DISCLAIMER in " ".join(out.split())
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import release_notes
    assert release_notes.RESEARCH_DISCLAIMER == RESEARCH_DISCLAIMER
    entry = "- " + "an entry that is long enough to matter " * 6 + "\n  continued\n"
    big = "## [9.9.9]\n\n### Added\n\n" + entry * 1200 + "### Fixed\n\n" + entry * 400
    out = notes(big)
    assert len(out) <= 125_000
    assert "https://example.org/CHANGELOG.md" in out and "Screening, not sign-off." in out
    kept = out.split("\n\n**", 1)[0]                 # the entries, before the note
    assert kept.endswith("  continued") and entry.strip() in kept
    assert notes("## [9.9.9]\n\nnothing\n", ver="1.2.3").startswith("No CHANGELOG.md section")
    # a cut just after a heading leaves the heading out with its entries
    room = 125_000 - 400
    fill = entry * ((room - 200) // len(entry))
    big = "## [9.9.9]\n\n### Added\n\n" + fill + "### Fixed\n\n- " + "x" * 9000 + "\n"
    kept = notes(big).split("\n\n**", 1)[0]
    assert kept.endswith("  continued") and "### Fixed" not in kept


def test_the_release_notes_keep_every_change_of_behaviour():
    """The Unreleased section as a release's notes: what the cut leaves out
    is fixes only. Security, Added, Changed and Removed come before Fixed
    (Keep a Changelog's order), and each of them fits whole."""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import release_notes
    text = _text(os.path.join(ROOT, "CHANGELOG.md")).replace("## [Unreleased]",
                                                             "## [9.9.9] - x", 1)
    body = release_notes.section(text, "9.9.9")
    heads = re.findall(r"^### (.+)$", body, flags=re.M)
    assert heads == ["Security", "Added", "Changed", "Removed", "Fixed"], heads
    out = release_notes.notes(text, "9.9.9", "https://example.org/CHANGELOG.md")
    assert len(out) <= 125_000
    before_fixed = body.split("\n### Fixed\n", 1)[0]
    assert out.startswith(before_fixed), "a change of behaviour was cut from the notes"
