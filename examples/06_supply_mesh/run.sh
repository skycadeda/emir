#!/usr/bin/env bash
# A supply mesh screened with rcc: the RC netlist simulated with every branch
# current saved, and every metric checked.
#
#   ./run.sh [OUT_DIR]
#
# When Xyce is on PATH: the N = 10 mesh (32 inverters) is written, simulated
# with every branch current printed, screened with the invented limits of
# process_screen.yaml (a few straps fail) and checked against
# expected/screen_n10.json; then screened again without --period, which is
# refused with no number (EMIR-E428: a transient needs the block's period),
# against expected/screen_n10_no_period.json (seconds).
#
# Works from a clone: PYTHON (default python3) needs numpy, scipy and PyYAML;
# emir is found through PYTHONPATH when it is not installed. Output goes to
# OUT_DIR (an argument), else $OUT, else ./out. SYNTHETIC inputs; Xyce, serial;
# screening, not sign-off.
# `sh run.sh` works too: this script needs bash (pipefail), so a POSIX sh
# hands it over before any bash syntax
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PY="${PYTHON:-python3}"
# a relative interpreter or output path is taken from where run.sh was started
case "$PY" in */*) PY="$(cd "$(dirname "$PY")" && pwd)/$(basename "$PY")" ;; esac
dir=""
for arg in "$@"; do
  case "$arg" in
    -*) echo "run.sh: unknown option $arg (it takes an output directory only)" >&2; exit 2 ;;
    *) if [ -n "$dir" ]; then
         echo "run.sh: one output directory only ($dir, then $arg)" >&2; exit 2
       fi
       dir=$arg ;;
  esac
done
OUT="${dir:-${OUT:-$HERE/out}}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
cd "$HERE"

if ! command -v Xyce > /dev/null 2>&1; then
  # the reason goes into OUT_DIR too, so a run that skips still says why there
  echo "== skipped: Xyce is not on PATH (the mesh is simulated)" | tee "$OUT/skipped.txt"
  echo "run.sh: done"
  exit 0
fi

echo "== 1. the N=10 mesh: its RC DSPF and the Xyce deck that prints every branch current"
"$PY" mesh.py xyce --n 10 --k 32 --out "$OUT/screen" > /dev/null
echo "== 2. simulate it (Xyce, serial)"
( cd "$OUT/screen" && Xyce -a rcc.cir > xyce.log 2>&1 ) \
  || { tail -n 30 "$OUT/screen/xyce.log" >&2; exit 1; }
echo "== 3. screen it with rcc, with invented limits (process_screen.yaml)"
( cd "$OUT/screen" && "$PY" -m emir run grid_rc.dspf --process "$HERE/process_screen.yaml" \
    --net VDD --cc-sim rcc.raw --period 1e-9 --sim xyce \
    --summary-out screen.json --html html > screen.log 2>&1 ) \
  || { tail -n 30 "$OUT/screen/screen.log" >&2; exit 1; }
"$PY" compare.py expected/screen_n10.json "$OUT/screen/screen.json"
echo "== 4. the same screen without --period: refused (EMIR-E428), no number"
rc=0
( cd "$OUT/screen" && "$PY" -m emir run grid_rc.dspf --process "$HERE/process_screen.yaml" \
    --net VDD --cc-sim rcc.raw --sim xyce --summary-out screen_no_period.json \
    > screen_no_period.log 2>&1 ) || rc=$?
[ "$rc" = 2 ] || { tail -n 30 "$OUT/screen/screen_no_period.log" >&2
                   echo "run.sh: exit $rc, not 2 (refused)" >&2; exit 1; }
"$PY" compare.py expected/screen_n10_no_period.json "$OUT/screen/screen_no_period.json"
echo "   chart: $OUT/screen/html/emir_chart.png"
echo "run.sh: done"
