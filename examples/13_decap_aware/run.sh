#!/usr/bin/env bash
# Decap-aware EM (--dynamic, --em-basis): decap-free against decap-aware.
#
#   ./run.sh [OUT_DIR] [--sweep] [--loop]
#
#   ./run.sh            offline: a small synthetic decap mesh screened once per
#                       EM basis, and `emir next --run-summary` (seconds); and,
#                       when Xyce is on PATH, RCC against decap-free and
#                       decap-aware at N = 20 with 8 decaps (about a minute); each
#                       checked against expected/
#   ./run.sh --sweep    also every case behind results/ (N = 20, 40, 70; no
#                       decap, 2 ohm and 20 ohm decaps; two resistive rails),
#                       written to OUT (a few minutes)
#   ./run.sh --loop     also the self-consistent loop with decaps behind
#                       results/decap_loop.* (sized, not sized, one simulated;
#                       about six minutes)
#
# Works from a clone: PYTHON (default python3) needs numpy, scipy and PyYAML;
# emir is found through PYTHONPATH when it is not installed. Output goes to
# OUT_DIR (an argument), else $OUT, else ./out. SYNTHETIC inputs; Xyce, serial;
# screening, not sign-off.
# `sh run.sh` works too: this script needs bash (arrays, pipefail), so a
# POSIX sh hands it over before any bash syntax
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PY="${PYTHON:-python3}"
# a relative interpreter or output path is taken from where run.sh was started
case "$PY" in */*) PY="$(cd "$(dirname "$PY")" && pwd)/$(basename "$PY")" ;; esac
sweep=0; loop=0; dir=""
for arg in "$@"; do
  case "$arg" in
    --sweep) sweep=1 ;;
    --loop) loop=1 ;;
    -*) echo "run.sh: unknown option $arg (use --sweep or --loop)" >&2; exit 2 ;;
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
COMPARE="$HERE/../06_big_block/compare.py"

echo "== 1. a small decap mesh, every EM basis, then emir next (synthetic, no simulator)"
"$PY" offline.py --out "$OUT/small" --json "$OUT/small.json"
"$PY" "$COMPARE" expected/decap_small.json "$OUT/small.json"

if command -v Xyce > /dev/null 2>&1; then
  echo "== 2. RCC against decap-free and decap-aware on Xyce, N=20, 8 decaps (synthetic, serial)"
  "$PY" decap_sweep.py --cases n20_2ohm --out "$OUT/sweep" --json "$OUT/xyce_n20.json" \
      --md "$OUT/xyce_n20.md" > /dev/null
  "$PY" "$COMPARE" expected/xyce_n20_decap.json "$OUT/xyce_n20.json"
  echo "   table: $OUT/xyce_n20.md"
else
  echo "== 2. skipped: Xyce is not on PATH (the RCC comparison needs it)"
fi

if [ "$sweep" = 1 ]; then
  echo "== 3. every case (Xyce, synthetic, serial)"
  "$PY" decap_sweep.py --cases all --out "$OUT/sweep_full" --csv "$OUT/decap.csv" \
      --md "$OUT/decap.md" --json "$OUT/decap.json" > /dev/null
  echo "   $OUT/decap.csv and $OUT/decap.md (results/ holds the committed copy)"
fi
if [ "$loop" = 1 ]; then
  echo "== 4. the self-consistent loop with decaps (Xyce, synthetic, serial)"
  "$PY" decap_loop.py --out "$OUT/loop" --csv "$OUT/decap_loop.csv" \
      --md "$OUT/decap_loop.md" --json "$OUT/decap_loop.json" > /dev/null
  echo "   $OUT/decap_loop.csv and $OUT/decap_loop.md (results/ holds the committed copy)"
fi
echo "run.sh: done"
