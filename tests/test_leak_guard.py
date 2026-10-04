"""scripts/leak_guard.py: the repo is clean, every public rule catches its
planted leak, private rules are read from a file outside the repository (and
caught when they match), the guard passes on its own source, and commit
messages and every commit's files are checked too.

The identifying patterns (a licensed PDK, internal projects) are private: this
file plants only made-up terms through a private rule file of its own. Every
leak string of a public rule is assembled from fragments at run time, so this
file never spells one (the guard scans it like any other file).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import zlib

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
GUARD = os.path.join(ROOT, "scripts", "leak_guard.py")

HOME = "/ho" + "me/someone/work"
LIC = "12" + "34@licsrv.example"          # an obviously fake port@host
PROP = "Propri" + "etary"
#: a made-up identifying term, guarded by a private rule file of the test's own
FAKE = "zz" + "fakepdk"
FAKE_RULES = ("fake-pdk\tany\t(?i)z[z]fakepdk\ta made-up licensed-PDK name\n"
              "fake-node\ttext\t(?i)(?<![\\w.])9[9]7(?![\\w.:])\ta made-up node number\n")


def _guard(*args, cwd=None, private=None):
    """The guard as a program. `private`: the text of a private rule file
    (None: no private rules at all, whatever this machine has)."""
    env = {k: v for k, v in os.environ.items() if k != "EMIR_LEAK_PRIVATE"}
    with tempfile.TemporaryDirectory() as d:
        env["EMIR_LEAK_PRIVATE"] = os.path.join(d, "none")
        if private is not None:
            env["EMIR_LEAK_PRIVATE"] = os.path.join(d, "private")
            with open(env["EMIR_LEAK_PRIVATE"], "w", encoding="utf-8") as f:
                f.write(private)
        return subprocess.run([sys.executable, GUARD, *args], cwd=cwd, env=env,
                              capture_output=True, text=True)


def _findings(root, private=None):
    """The guard's findings on `root`. In an unpacked sdist (no git, a
    PKG-INFO) the guard walks the files on disk, and the bytecode this test
    run wrote there (newer than the sdist's build, PKG-INFO's time) is not
    part of what was published: it is left out. A plain copy of the tree,
    with no PKG-INFO to date it, gets no such allowance."""
    p = _guard("--root", root, private=private)
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    pkg_info = os.path.join(root, "PKG-INFO")
    if p.returncode != 1 or os.path.exists(os.path.join(root, ".git")) \
            or not os.path.isfile(pkg_info):
        return p.returncode, lines, p.stderr
    built = os.path.getmtime(pkg_info)

    def ours(ln):
        # every finding on a bytecode file this run wrote: its type, and the
        # strings constant folding put in it (this file's fragments, joined)
        rel = ln.split(":", 1)[0]
        if "__pycache__" not in rel.split("/") and not rel.endswith((".pyc", ".pyo")):
            return False
        path = os.path.join(root, rel)
        # files the sdist shipped keep their source times (PKG-INFO is
        # written last, at the build): anything newer came after it
        return os.path.isfile(path) and os.path.getmtime(path) > built
    left = [ln for ln in lines if not ours(ln)]
    return (1 if left else 0), left, p.stderr


#: this machine's private rule file, when the maintainer keeps one
PRIVATE = os.environ.get("EMIR_LEAK_PRIVATE") or os.path.join(ROOT, ".leak-guard-private")


def _private_text():
    return open(PRIVATE, encoding="utf-8").read() if os.path.isfile(PRIVATE) else None


def test_repo_is_clean():
    rc, lines, err = _findings(ROOT, private=_private_text())
    assert rc == 0, "\n".join(lines) + err


def test_repo_is_clean_under_the_private_rules():
    """The shipped guard, its tests and the whole tree match none of the
    private rules (this machine's file; skipped where there is none)."""
    if _private_text() is None:
        pytest.skip("no private rule file on this machine")
    p = _guard("--root", ROOT, "--require-private", private=_private_text())
    assert p.returncode == 0, p.stdout + p.stderr
    assert "public rules only" not in p.stdout


def test_the_private_rule_file_is_never_committed_or_shipped():
    ignore = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read().split()
    assert "/.leak-guard-private" in ignore
    manifest = open(os.path.join(ROOT, "MANIFEST.in"), encoding="utf-8").read()
    assert "leak-guard-private" not in manifest


def test_an_unpacked_sdist_is_judged_on_what_it_shipped(tmp_path):
    """pytest from an unpacked sdist failed `test_repo_is_clean` on the
    __pycache__ its own earlier tests wrote (no .git: the guard walks the
    disk). Bytecode newer than the sdist's PKG-INFO is this run's; older
    bytecode was shipped, and still fails."""
    import time
    (tmp_path / "PKG-INFO").write_text("Metadata-Version: 2.4\nName: emir\n")
    (tmp_path / "m.py").write_text("x = 1\n")
    old = time.time() - 3600
    os.utime(tmp_path / "PKG-INFO", (old, old))
    pc = tmp_path / "__pycache__"
    pc.mkdir()
    # this run's bytecode, with a leak string constant folding put there
    (pc / "m.cpython-312.pyc").write_bytes(b"\0fresh " + HOME.encode())
    rc, lines, _ = _findings(str(tmp_path))
    assert rc == 0, lines
    shipped = pc / "n.cpython-312.pyc"
    shipped.write_bytes(b"\0shipped")
    os.utime(shipped, (old - 60, old - 60))
    rc, lines, _ = _findings(str(tmp_path))
    assert rc == 1 and len(lines) == 1 and "n.cpython-312.pyc" in lines[0]
    # with no PKG-INFO to date the files, the guard's verdict stands
    (tmp_path / "PKG-INFO").unlink()
    rc, lines, _ = _findings(str(tmp_path))
    assert rc == 1 and len(lines) == 3          # both files, and the string in one


def test_allowlist_has_no_exemption_for_the_guard():
    allow = open(os.path.join(ROOT, ".leak-guard-allow"), encoding="utf-8").read()
    entries = [ln for ln in allow.splitlines() if ln.strip() and not ln.startswith("#")]
    assert not any("leak_guard" in ln for ln in entries), entries


#: (planted text, rule id) -- one or more fixtures per public rule
PLANTED = [
    (f"path = '{HOME}/x'\n", "site-path"),
    ("tools in /ca" + "d/bin\n", "site-path"),
    ("data in /pro" + "j/chip\n", "site-path"),
    ("see /Us" + "ers/someone/x.dspf\n", "user-path"),
    ("see C:" + "\\Us" + "ers\\someone\\x.dspf\n", "user-path"),
    ("see C:" + "/Us" + "ers/someone/x.dspf\n", "user-path"),
    (f"export CDS_LIC" f"_FILE={LIC}\n", "licence-env"),
    ("export LM_LICENSE" + "_FILE=x\n", "licence-env"),
    (f"server {LIC}\n", "licence-server"),
    ("for the " + "custo" + "mer\n", "deployment-wording"),
    ("see plan " + "§" + "4\n", "internal-plan"),
    (f'license = {{text = "{PROP}"}}\n', "closed-licence"),
    (f"license: {PROP.lower()}\n", "closed-licence"),
    ("spectre: /to" + "ols/bin/spectre\n", "site-install"),
    ("PATH=/to" + "ols/cadence/bin\n", "site-install"),
    ("cd /inst" + "all/x\n", "site-install"),
    ("root /opt/inst" + "all/" + "FOUNDRY/x\n", "site-install"),
    ("tools in " + "I" + "C618/bin\n", "site-install"),
    ("the " + "SPECTR" + "E201 tree\n", "site-install"),
    ("root " + "I" + "C618_ISR/bin\n", "site-install"),
    ("root " + "i" + "c618/bin\n", "site-install"),
    ("the " + "pv" + "s221 tree\n", "site-install"),
]


@pytest.mark.parametrize("text,rule", PLANTED, ids=[f"{r}-{i}" for i, (_, r) in enumerate(PLANTED)])
def test_planted_leak_is_caught(tmp_path, text, rule):
    (tmp_path / "f.txt").write_text(text, encoding="utf-8")
    p = _guard("--root", str(tmp_path))
    assert p.returncode == 1, p.stdout
    assert f"f.txt:1: {rule}" in p.stdout, p.stdout


@pytest.mark.parametrize("text", [
    "Algorithm ported from the Sky" + "cadeda Analog Framework (SAF).\n",
    "the open PDKs: sky130, gf180mcu, ihp-sg13g2; any generic PDK\n",
    "Cadence Virtuoso, Spectre and Quantus\n",
    "$CDSHOME/to" + "ols/bin/spectre is the standard layout\n",
    "Virtuoso I" + "C6.1.8 or later\n",
    "github.com/sky" + "cadeda/emir\n",
    "mail someone@example.com\n",
    "basic" + "618 and topic" + "221 are not release tags\n",
])
def test_clean_text_passes(tmp_path, text):
    (tmp_path / "f.txt").write_text(text, encoding="utf-8")
    p = _guard("--root", str(tmp_path))
    assert p.returncode == 0, p.stdout


# ------------------------------------------------------------ private rules


def test_private_rules_are_read_from_their_file_and_caught(tmp_path):
    (tmp_path / "f.txt").write_text(f"# extracted with {FAKE.upper()}\nthe 99" + "7 flow\n")
    p = _guard("--root", str(tmp_path))                     # no private file: passes
    assert p.returncode == 0 and "public rules only" in p.stdout, p.stdout
    p = _guard("--root", str(tmp_path), private=FAKE_RULES)
    assert p.returncode == 1, p.stdout
    assert "f.txt:1: fake-pdk" in p.stdout and "f.txt:2: fake-node" in p.stdout
    # the rule id is reported, never the private pattern itself
    assert "z[z]" not in p.stdout


def test_the_private_file_at_the_root_is_read(tmp_path):
    (tmp_path / "f.txt").write_text(f"{FAKE}\n")
    (tmp_path / ".leak-guard-private").write_text(FAKE_RULES)
    env = {k: v for k, v in os.environ.items() if k != "EMIR_LEAK_PRIVATE"}
    p = subprocess.run([sys.executable, GUARD, "--root", str(tmp_path), "f.txt"],
                       cwd=tmp_path, env=env, capture_output=True, text=True)
    assert p.returncode == 1 and "fake-pdk" in p.stdout, p.stdout


@pytest.mark.parametrize("bad", ["fake\tany\t(unclosed\twhy\n", "fake\tsometimes\tx\twhy\n",
                                 "only two\tfields\n"])
def test_a_malformed_private_file_is_an_error(tmp_path, bad):
    (tmp_path / "f.txt").write_text("x\n")
    p = _guard("--root", str(tmp_path), private=bad)
    assert p.returncode == 2, p.stdout + p.stderr


def test_a_private_rule_can_be_limited_to_some_files(tmp_path):
    """A number that leaks in a page is a coordinate in a drawing: the fifth
    field limits a rule to the files its globs match."""
    rules = "fake-num\ttext\t(?<![\\w.])3\\.14[1](?![\\w.])\tmade up\tdocs/*.md,README.md\n"
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("ratio 3.141\n")
    (tmp_path / "fig.svg").write_text('<g transform="translate(3.141 2)"/>\n')
    p = _guard("--root", str(tmp_path), private=rules)
    assert p.returncode == 1 and "docs/a.md:1: fake-num" in p.stdout
    assert "fig.svg" not in p.stdout


def test_require_private(tmp_path):
    (tmp_path / "f.txt").write_text("x\n")
    assert _guard("--root", str(tmp_path), "--require-private").returncode == 2
    assert _guard("--root", str(tmp_path), "--require-private",
                  private=FAKE_RULES).returncode == 0


def test_text_rules_skip_binary_files(tmp_path):
    (tmp_path / "img.bin").write_bytes(b"\0\x89 99" + b"7 " + b"\0")
    assert _guard("--root", str(tmp_path), private=FAKE_RULES).returncode == 0
    (tmp_path / "img.bin").write_bytes(b"\0\x89" + FAKE.encode() + b"\0")
    assert _guard("--root", str(tmp_path), private=FAKE_RULES).returncode == 1


def test_text_inside_a_compressed_pdf_is_read(tmp_path):
    body = zlib.compress(f"BT ({FAKE}) Tj ET".encode())
    (tmp_path / "x.pdf").write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Filter /FlateDecode >>\nstream\n"
                                     + body + b"\nendstream\nendobj\n%%EOF\n")
    assert _guard("--root", str(tmp_path)).returncode == 0
    p = _guard("--root", str(tmp_path), private=FAKE_RULES)
    assert p.returncode == 1 and "x.pdf (stream 0)" in p.stdout, p.stdout


def test_every_commit_is_checked_not_only_the_tip(tmp_path):
    """A leak added in one commit and removed in the next stays in the
    history: `--commit` checks each commit's own files and message."""
    def git(*a):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                               *a], cwd=tmp_path, check=True, capture_output=True, text=True)
    git("init", "-q")
    (tmp_path / "a.txt").write_text(f"see {HOME}/x\n")
    git("add", "a.txt")
    git("commit", "-q", "-m", "add a")
    first = git("rev-parse", "HEAD").stdout.strip()
    (tmp_path / "a.txt").write_text("clean now\n")
    git("commit", "-q", "-am", "clean a")
    second = git("rev-parse", "HEAD").stdout.strip()
    assert _guard("--root", str(tmp_path)).returncode == 0          # the tip is clean
    p = _guard("--root", str(tmp_path), "--commit", first, "--commit", second)
    assert p.returncode == 1 and f"{first[:12]}:a.txt:1: site-path" in p.stdout, p.stdout
    git("commit", "-q", "--allow-empty", "-m", f"note {FAKE}")
    third = git("rev-parse", "HEAD").stdout.strip()
    p = _guard("--root", str(tmp_path), "--commit", third, private=FAKE_RULES)
    assert p.returncode == 1 and "<commit message>:1: fake-pdk" in p.stdout, p.stdout


def test_a_merge_resolution_is_checked_as_its_own_commit(tmp_path):
    """What a merge resolution writes exists in no parent: `--commit` on the
    merge checks every file that differs from a parent (`git diff-tree`
    lists none of a merge's files by itself)."""
    def git(*a):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                               *a], cwd=tmp_path, check=True, capture_output=True, text=True)
    git("init", "-q", "-b", "main")
    (tmp_path / "a.txt").write_text("one\n")
    git("add", "a.txt")
    git("commit", "-q", "-m", "a")
    git("checkout", "-q", "-b", "side")
    (tmp_path / "a.txt").write_text("side\n")
    git("commit", "-q", "-am", "side")
    git("checkout", "-q", "main")
    (tmp_path / "a.txt").write_text("main\n")
    git("commit", "-q", "-am", "main")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "merge",
                    "-q", "side"], cwd=tmp_path, capture_output=True)   # a conflict: exit 1
    (tmp_path / "a.txt").write_text(f"resolved, see {HOME}/x\n")      # only in the merge
    git("add", "a.txt")
    git("commit", "-q", "-m", "merge side")
    merge = git("rev-parse", "HEAD").stdout.strip()
    assert len(git("rev-list", "--parents", "-n", "1", merge).stdout.split()) == 3
    p = _guard("--root", str(tmp_path), "--commit", merge)
    assert p.returncode == 1 and f"{merge[:12]}:a.txt:1: site-path" in p.stdout, p.stdout


@pytest.mark.parametrize("name,ok", [("tech.tf", False), ("x.ict", False),
                                     ("pdk.layermap", False), ("display.drf", False),
                                     ("emir_display.drf", True), ("mod.pyc", False),
                                     ("mod.pyo", False), ("__pycache__/m.txt", False)])
def test_file_types(tmp_path, name, ok):
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("nothing to see\n")
    p = _guard("--root", str(tmp_path))
    assert (p.returncode == 0) == ok, p.stdout


def test_compiled_python_is_refused_even_when_force_added(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / ".gitignore").write_text("__pycache__/\n")
    pc = tmp_path / "pkg" / "__pycache__"
    pc.mkdir(parents=True)
    (pc / "m.cpython-312.pyc").write_bytes(b"\0\x01" + HOME.encode())
    git("add", "-f", "pkg/__pycache__/m.cpython-312.pyc")
    p = _guard("--root", str(tmp_path))
    assert p.returncode == 1 and "compiled Python" in p.stdout, p.stdout


DOT_AND_DOC_DIRS = [".claude/skills/emir/SKILL.md", ".github/workflows/ci.yml",
                    "docs/guide.md", "examples/01_quickstart/run.sh", "blog/post.md"]


@pytest.mark.parametrize("git_repo", [False, True])
def test_dot_and_doc_directories_are_scanned(tmp_path, git_repo):
    for rel in DOT_AND_DOC_DIRS:
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"see {HOME}/run\n")
    if git_repo:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    p = _guard("--root", str(tmp_path))
    assert p.returncode == 1
    for rel in DOT_AND_DOC_DIRS:
        assert f"{rel}:1: site-path" in p.stdout, (rel, p.stdout)


def test_allowlist_needs_a_reason_and_can_be_narrow(tmp_path):
    (tmp_path / "doc.md").write_text(f"see {HOME}/a\nand {HOME}/b\n")
    allow = tmp_path / ".leak-guard-allow"
    allow.write_text("doc.md  site-path\n")                      # no reason
    assert _guard("--root", str(tmp_path)).returncode == 2
    allow.write_text("doc.md  site-path  text=/a  # an example path\n")
    p = _guard("--root", str(tmp_path))
    assert p.returncode == 1 and "doc.md:2:" in p.stdout and "doc.md:1:" not in p.stdout
    allow.write_text("doc.md  site-path  # example paths in the docs\n")
    assert _guard("--root", str(tmp_path)).returncode == 0


def test_binary_files_only_get_the_long_patterns(tmp_path):
    (tmp_path / "img.bin").write_bytes(b"\0\x89" + LIC.encode() + b"\0")
    assert _guard("--root", str(tmp_path)).returncode == 0
    (tmp_path / "img.bin").write_bytes(b"\0\x89" + HOME.encode() + b"\0")
    assert _guard("--root", str(tmp_path)).returncode == 1


def test_staged_content_is_what_counts(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "a.py").write_text(f"X = '{HOME}'\n")
    git("add", "a.py")
    (tmp_path / "a.py").write_text("X = 'clean in the working tree only'\n")
    p = _guard("--root", str(tmp_path), "--staged")
    assert p.returncode == 1 and "a.py:1: site-path" in p.stdout


# ------------------------------------------------------------ the guard itself

#: every literal a public rule searches for, assembled here so neither file
#: spells it
BANNED = ["CDS_LIC" + "_FILE", "LM_LICENSE" + "_FILE", "/ho" + "me/", "/ca" + "d/",
          "/pro" + "j/", "/Us" + "ers/", "custo" + "mer", "plan " + "§", PROP,
          "/inst" + "all/", "/to" + "ols/", "SPECTR" + "E2", "I" + "C618"]


#: the rules that match case-sensitively (a site's versioned install directory
#: names); the rest match in any case
CASE_SENSITIVE = {"SPECTR" + "E2", "I" + "C618"}


def test_guard_source_never_spells_what_it_searches_for():
    src = open(GUARD, encoding="utf-8").read()
    for needle in BANNED:
        if needle in CASE_SENSITIVE:
            assert needle not in src, needle
        else:
            assert needle.lower() not in src.lower(), needle


def test_guard_passes_on_its_own_source_with_no_allowlist(tmp_path):
    os.makedirs(tmp_path / "scripts")
    shutil.copy(GUARD, tmp_path / "scripts" / "leak_guard.py")
    shutil.copy(__file__, tmp_path / "test_leak_guard.py")     # and on this file
    p = _guard("--root", str(tmp_path), private=_private_text())
    assert p.returncode == 0, p.stdout


# ------------------------------------------------------------ commit messages


def test_commit_message_is_scanned(tmp_path):
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text(f"Fix the via check\n\nFound on a {FAKE} block in {HOME}.\n")
    p = _guard("--root", str(tmp_path), "--commit-msg", str(msg), private=FAKE_RULES)
    assert p.returncode == 1 and "<commit message>:3: fake-pdk" in p.stdout, p.stdout
    assert "<commit message>:3: site-path" in p.stdout


def test_commit_message_comments_and_diff_are_not_the_message(tmp_path):
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("Fix the via check\n"
                   f"# Please enter the commit message ({HOME} is only a comment)\n"
                   "# ------------------------ >8 ------------------------\n"
                   f"diff --git a/x b/x\n+{FAKE}\n")
    p = _guard("--root", str(tmp_path), "--commit-msg", str(msg), private=FAKE_RULES)
    assert p.returncode == 0, p.stdout


def test_pre_commit_runs_the_guard_on_the_message():
    cfg = yaml.safe_load(open(os.path.join(ROOT, ".pre-commit-config.yaml")))
    assert "commit-msg" in cfg.get("default_install_hook_types", [])
    hooks = [h for r in cfg["repos"] for h in r["hooks"]]
    msg = [h for h in hooks if "commit-msg" in h.get("stages", [])]
    assert len(msg) == 1 and "--commit-msg" in msg[0]["entry"]
    assert "leak_guard.py" in msg[0]["entry"]
    files = [h for h in hooks if h["id"] == "leak-guard"]
    assert files and files[0].get("stages") == ["pre-commit"]
