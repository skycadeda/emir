#!/bin/sh
# Build the release artifacts from this source tree:
#
#   dist/emir-<ver>-py3-none-any.whl              pip-installable wheel
#   dist/emir-<ver>-linux-x86_64.tar.gz           OFFLINE bundle: standalone
#                                                 Python + all wheels + bin/emir
#   dist/SHA256SUMS                               checksums of both
#
# This runs in CI (the release workflow), and its output is attached to a
# GitHub Release -- never committed. It needs network access ONCE, here, to
# fetch python-build-standalone and the wheels; the bundle itself installs and
# runs with no network and no system Python.
#
#   sh scripts/build_bundle.sh [--pyver 3.11.13 --python-tag 20250818 --sha256 <hex>
#                               --full-sha256 <hex>]
#
# Overriding --pyver or --python-tag requires --sha256 and --full-sha256: the
# checksums of cpython-<pyver>+<tag>-x86_64-unknown-linux-gnu-install_only.tar.gz
# and of its -pgo+lto-full.tar.zst (whose python/licenses/ the bundle ships: the
# install_only archive carries no licence text of the libraries it links) from
# that release's SHA256SUMS. Needs zstd. A local run needs EMIR_ALLOW_LOCAL_BUNDLE=1.
set -eu

HERE=$(cd "$(dirname "$0")/.." && pwd -P)
VER=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$HERE/emir/__init__.py")
PYVER=3.11.13
PBS_TAG=20250818          # python-build-standalone release tag
# sha256 of the default asset, from that release's SHA256SUMS
PBS_SHA256=b3d07471abdf1b3d2867dd44f095c891fb072bab5667b9322355546f9f9c5dda
# ... and of the same build's -pgo+lto-full.tar.zst (only its licence texts are used)
PBS_FULL_SHA256=fde49f6369f4d36a0d6aaedde461c609d280a325f794301ec5fb815f497f5057
ARCH=x86_64-unknown-linux-gnu
overridden=0
sha_given=0
full_given=0
while [ $# -gt 0 ]; do
  case "$1" in
    --pyver) PYVER=$2; overridden=1; shift 2;;
    --python-tag) PBS_TAG=$2; overridden=1; shift 2;;
    --sha256) PBS_SHA256=$2; sha_given=1; shift 2;;
    --full-sha256) PBS_FULL_SHA256=$2; full_given=1; shift 2;;
    *) echo "unknown option $1" >&2; exit 2;;
  esac
done
if [ "$overridden" = 1 ] && [ "$sha_given$full_given" != 11 ]; then
  echo "build_bundle: --pyver/--python-tag changed the assets; pass their --sha256 and --full-sha256 too" >&2
  exit 2
fi
if [ -z "${CI:-}" ] && [ "${EMIR_ALLOW_LOCAL_BUNDLE:-}" != 1 ]; then
  echo "build_bundle: meant for CI (the release workflow). Set EMIR_ALLOW_LOCAL_BUNDLE=1 to build locally." >&2
  exit 2
fi
if [ -z "$VER" ]; then
  echo "build_bundle: no __version__ in emir/__init__.py" >&2; exit 2
fi

# Isolation from the build host: no user site-packages, no pip config, no
# --user installs, no .pyc files written into the staged tree.
export PYTHONNOUSERSITE=1 PIP_USER=0 PIP_CONFIG_FILE=/dev/null
export PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1

BUILD=$HERE/build
DIST=$HERE/dist
NAME=emir-$VER-linux-x86_64     # tarball name
TOP=emir-$VER                   # directory inside it
STAGE=$BUILD/$TOP
rm -rf "$STAGE" "$BUILD/venv" "$BUILD/wheels"
mkdir -p "$STAGE" "$DIST" "$BUILD/cache" "$BUILD/wheels"

echo "== 1. wheel (in a throw-away build venv)"
${PYTHON:-python3} -m venv "$BUILD/venv"
"$BUILD/venv/bin/python" -m pip install -q "build>=1"
rm -f "$DIST"/emir-"$VER"-*.whl
"$BUILD/venv/bin/python" -m build --wheel --outdir "$DIST" "$HERE"
WHEEL=$DIST/emir-$VER-py3-none-any.whl
if [ ! -f "$WHEEL" ]; then
  echo "build_bundle: expected $WHEEL" >&2; exit 1
fi
echo "   $WHEEL"

echo "== 2. standalone Python $PYVER ($PBS_TAG), checksum-verified"
PBS=cpython-$PYVER+$PBS_TAG-$ARCH-install_only.tar.gz
URL=https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG/$PBS
if [ ! -f "$BUILD/cache/$PBS" ]; then
  curl -L --fail -o "$BUILD/cache/$PBS.part" "$URL"
  mv "$BUILD/cache/$PBS.part" "$BUILD/cache/$PBS"
fi
echo "$PBS_SHA256  $BUILD/cache/$PBS" | sha256sum -c -
tar xzf "$BUILD/cache/$PBS" -C "$STAGE"        # extracts to $STAGE/python
PY=$STAGE/python/bin/python3
"$PY" --version

echo "== 2b. the licence texts of what that Python links (from the same build's full archive)"
FULL=cpython-$PYVER+$PBS_TAG-$ARCH-pgo+lto-full.tar.zst
FURL=https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG/$(echo "$FULL" | sed 's/+/%2B/g')
if [ ! -f "$BUILD/cache/$FULL" ]; then
  curl -L --fail -o "$BUILD/cache/$FULL.part" "$FURL"
  mv "$BUILD/cache/$FULL.part" "$BUILD/cache/$FULL"
fi
echo "$PBS_FULL_SHA256  $BUILD/cache/$FULL" | sha256sum -c -
LIC=$STAGE/licenses/python-build-standalone
rm -rf "$BUILD/full" && mkdir -p "$BUILD/full" "$LIC"
zstd -dc "$BUILD/cache/$FULL" | tar -x -C "$BUILD/full" python/licenses python/PYTHON.json
cp "$BUILD"/full/python/licenses/* "$BUILD/full/python/PYTHON.json" "$LIC/"
for need in openssl ncurses libffi tcl bzip2 liblzma libedit zlib sqlite; do
  if ! ls "$LIC" | grep -qi "$need"; then
    echo "build_bundle: no licence text for $need in $FULL" >&2; exit 1
  fi
done
cat > "$STAGE/licenses/GCC-RUNTIME.txt" <<'TXT'
numpy.libs/ and scipy.libs/ (under python/lib/python3.*/site-packages/) hold the
GCC runtime libraries libgfortran (GPL-3.0 with the GCC Runtime Library Exception)
and libquadmath (LGPL-2.1), as the numpy and scipy wheels ship them. Their source
is GCC's: https://gcc.gnu.org/ (git://gcc.gnu.org/git/gcc.git); the wheels' build
of them: https://github.com/MacPython/openblas-libs. The GCC version is in each
library's .dist-info and in `strings <lib> | grep GCC`.
TXT

echo "== 3. dependency wheels (for this Python, manylinux)"
DEPS="numpy>=1.24 scipy>=1.10 pyyaml>=6.0 matplotlib>=3.6"
# shellcheck disable=SC2086
"$PY" -m pip download -q -d "$BUILD/wheels" $DEPS \
    --only-binary=:all: --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 \
    --platform manylinux_2_28_x86_64 --python-version "${PYVER%.*}"

echo "== 4. install everything into the bundled Python (offline from the wheel dir)"
# shellcheck disable=SC2086
"$PY" -m pip install -q --no-index --find-links "$BUILD/wheels" $DEPS
"$PY" -m pip install -q --no-index --no-deps "$WHEEL"
# the bundled interpreter itself must have the dependencies -- not the host's
# user site, which is how a bundle can pass its self-test here and fail elsewhere
"$PY" -s -c 'import numpy, scipy, yaml, matplotlib, emir; print("   deps ok, emir", emir.__version__)'

echo "== 5. lay out the bundle"
cp -r "$HERE/bin" "$STAGE/"
# the licence files are not optional: a missing one stops the build
for f in README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md CHANGELOG.md; do
  cp "$HERE/$f" "$STAGE/"
done
cp -r "$HERE/docs" "$STAGE/docs"
# agent/ holds the Cadence integration a bundle user loads from EMIR_HOME
# (agent/cadence/skill/emir_init.il), the schemas and the error catalog
cp -r "$HERE/agent" "$STAGE/agent"
# pdks/: the TEMPLATE and the open PDKs' process files the docs tell a user to
# copy; examples/: their inputs, scripts and expected results (not outputs)
cp -r "$HERE/pdks" "$STAGE/pdks"
cp -r "$HERE/examples" "$STAGE/examples"
rm -rf "$STAGE"/examples/*/out "$STAGE"/examples/*/emir_out
mkdir -p "$STAGE/wheels" && cp "$WHEEL" "$BUILD"/wheels/*.whl "$STAGE/wheels/"
# the launcher finds python/bin/python3 and runs `-m emir`; keep a source copy
# too so `import emir` works without site-packages on PATH
cp -r "$HERE/emir" "$STAGE/emir"
find "$STAGE/emir" -name __pycache__ -prune -exec rm -rf {} +
# strip what nobody needs at run time
rm -rf "$STAGE/python/lib/python${PYVER%.*}/test" "$STAGE/python/lib/python${PYVER%.*}/idlelib" \
       "$STAGE/python/lib/python${PYVER%.*}/tkinter" "$STAGE/python/share"

echo "== 6. self-test the bundle in place (from /, so nothing on the cwd can shadow the package)"
(cd / && "$STAGE/bin/emir" selftest)
# what the docs tell a bundle user to run: a process file checked on its own,
# from the bundle's pdks/ (pdk-onboarding.md)
(cd "$STAGE" && bin/emir check --process pdks/TEMPLATE.yaml > /dev/null)
(cd "$STAGE" && bin/emir check --process pdks/sky130.yaml > /dev/null)
"$STAGE/bin/emir" gui-install > /dev/null
"$STAGE/bin/emir" info | head -3
# skill/ carries absolute paths of THIS machine; users regenerate it with
# `bin/emir gui-install` after untarring. Remove every cache the test left.
rm -rf "$STAGE/skill"
find "$STAGE" -name __pycache__ -prune -exec rm -rf {} +
find "$STAGE" -name "*.pyc" -delete

echo "== 6b. make the tree relocatable: no path of this machine in it"
# pip records where each wheel came from (direct_url.json: the build dist/)
find "$STAGE/python" -name direct_url.json -path "*.dist-info/*" -delete
# and writes console-script launchers (python/bin/emir, f2py, pip ...) that
# name this machine's interpreter -- in the shebang for a short build path,
# on the second line of a sh trampoline for a long one. Both become a
# trampoline to the python3 next to the script, wherever the bundle is
# untarred (scripts/relocate_launchers.sh says how).
sh "$HERE/scripts/relocate_launchers.sh" "$STAGE/python/bin" "$STAGE"
if grep -rlI -- "$STAGE" "$STAGE" > "$BUILD/leaks.txt"; then
  echo "build_bundle: the staged tree still names $STAGE in:" >&2
  cat "$BUILD/leaks.txt" >&2
  exit 1
fi
"$STAGE/python/bin/emir" --version

echo "== 7. tarball (owner root, numeric ids: nothing of the build host leaks)"
(cd "$BUILD" && tar --owner=0 --group=0 --numeric-owner -czf "$DIST/$NAME.tar.gz" "$TOP")
(cd "$DIST" && sha256sum "$NAME.tar.gz" "$(basename "$WHEEL")" > SHA256SUMS)
ls -lh "$DIST"
echo
echo "release artifacts in $DIST:"
echo "  $NAME.tar.gz   -- offline bundle: untar, run bin/emir selftest"
echo "  $(basename "$WHEEL")   -- for sites with their own Python >= 3.10"
echo "  SHA256SUMS"
