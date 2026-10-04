#!/usr/bin/env bash
# Big blocks (ADC top, PLL top): the CC + R-only route at scale.
#
#   ./run.sh [OUT_DIR] [--big | --sweep | --selfconsist]
#
#   ./run.sh            offline: streamed vs dense superposition on a synthetic
#                       mesh (no simulator), and, when Xyce is on PATH, RCC
#                       against CC + cc-grid at one small size, the waveform
#                       self-consistent loop there, and that mesh screened
#                       through the loop with invented limits
#                       (process_screen.yaml: a few items fail); each checked
#                       against expected/ (seconds)
#   ./run.sh --big      also the 92 k-node, 2 000-tap case the dense path could
#                       not hold (a few minutes; appends to out/stream_bench.csv)
#   ./run.sh --sweep    the full Xyce sweep behind results/ (about an hour)
#   ./run.sh --selfconsist  the waveform self-consistent loop behind
#                       results/selfconsist_waveform.csv (about ten minutes;
#                       one simulator at a time; ngspice for one case).
#                       results/selfconsist.csv is the record of the
#                       average-droop loop it replaced (an earlier version of
#                       selfconsist.py, not in this repository)
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
big=0; sweep=0; sc=0; dir=""
for arg in "$@"; do
  case "$arg" in
    --big) big=1 ;;
    --sweep) sweep=1 ;;
    --selfconsist) sc=1 ;;
    -*) echo "run.sh: unknown option $arg (use --big, --sweep or --selfconsist)" >&2; exit 2 ;;
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

echo "== 1. streamed superposition == dense (synthetic mesh, no simulator)"
"$PY" stream_check.py --out "$OUT/stream" --json "$OUT/stream.json" > /dev/null
"$PY" compare.py expected/stream_small.json "$OUT/stream.json"

if command -v Xyce > /dev/null 2>&1; then
  echo "== 2. RCC vs CC + cc-grid on Xyce, N=10 (synthetic, serial)"
  "$PY" sweep.py --sizes 10 --out "$OUT/sweep" --json "$OUT/sweep_n10.json" \
      --md "$OUT/sweep_n10.md" > /dev/null
  "$PY" compare.py expected/xyce_n10.json "$OUT/sweep_n10.json"
  echo "   table: $OUT/sweep_n10.md"
  echo "== 3. the waveform self-consistent loop against RCC on Xyce, N=10"
  "$PY" selfconsist.py --cases k32_n10 --out "$OUT/selfconsist" \
      --json "$OUT/selfconsist_n10.json" --md "$OUT/selfconsist_n10.md" > /dev/null
  "$PY" compare.py expected/selfconsist_n10.json "$OUT/selfconsist_n10.json"
  echo "   table: $OUT/selfconsist_n10.md"
  echo "== 4. the N=10 mesh screened through the loop, with invented limits (process_screen.yaml)"
  "$PY" mesh.py xyce --n 10 --k 32 --out "$OUT/screen" > /dev/null
  ( cd "$OUT/screen" && "$PY" -m emir run grid_r.dspf --process "$HERE/process_screen.yaml" \
      --net VDD --mode cc-grid --self-consistent --sim xyce --netlist cc_tb.cir --vdd 1.0 \
      --period 1e-9 --summary-out screen.json --html html > screen.log 2>&1 ) \
    || { tail -n 30 "$OUT/screen/screen.log" >&2; exit 1; }
  "$PY" compare.py expected/screen_n10.json "$OUT/screen/screen.json"
  echo "   chart: $OUT/screen/html/emir_chart.png"
else
  echo "== 2. skipped: Xyce is not on PATH (the RCC vs CC comparison needs it)"
fi

if [ "$big" = 1 ]; then
  echo "== 4. the 92 k-node, 181 k-resistor, 2 000-tap case"
  "$PY" stream_check.py --n 300 --k 2000 --nt 501 --out "$OUT/big" \
      --json "$OUT/big.json" --csv "$OUT/stream_bench.csv" > /dev/null
  echo "   row appended to $OUT/stream_bench.csv"
fi

if [ "$sweep" = 1 ]; then
  echo "== 5. the full sweep (Xyce, synthetic, serial)"
  "$PY" sweep.py --sizes 10,20,40,70,100,140,200 --variants stiff,k256,contact0,nooverlap \
      --out "$OUT/sweep_full" --csv "$OUT/sweep.csv" --md "$OUT/sweep.md" \
      --json "$OUT/sweep.json" > /dev/null
  echo "   $OUT/sweep.csv and $OUT/sweep.md (results/ holds the committed copy)"
fi
if [ "$sc" = 1 ]; then
  echo "== 6. the waveform self-consistent loop against RCC (Xyce, synthetic, serial)"
  "$PY" selfconsist.py --cases k32_n10,k32_n20,hd16b,k32_n40 --nocap \
      --out "$OUT/selfconsist_all" --json "$OUT/sc_a.json" > /dev/null
  "$PY" selfconsist.py --cases null_n20,one,vss_n10,c0_n20 --nocap \
      --out "$OUT/selfconsist_all" --json "$OUT/sc_b.json" > /dev/null
  "$PY" selfconsist.py --cases hd16b --guard --out "$OUT/selfconsist_all" \
      --json "$OUT/sc_g.json" > /dev/null
  "$PY" selfconsist.py --cases k32_n20 --reltol 1e-3 --out "$OUT/selfconsist_all" \
      --json "$OUT/sc_r.json" > /dev/null
  "$PY" selfconsist.py --cases p4_n20,hd16b_p4,hd16x --nocap --out "$OUT/selfconsist_all" \
      --json "$OUT/sc_v.json" > /dev/null
  "$PY" selfconsist.py --cases hd16b_c20,hd16b_c20rc,k32_n10_ng,k32_n10_long --nocap \
      --out "$OUT/selfconsist_all" --json "$OUT/sc_c.json" > /dev/null
  "$PY" selfconsist.py --cases "" --include "$OUT/sc_a.json" --include "$OUT/sc_b.json" \
      --include "$OUT/sc_g.json" --include "$OUT/sc_r.json" --include "$OUT/sc_v.json" \
      --include "$OUT/sc_c.json" \
      --csv "$OUT/selfconsist_waveform.csv" \
      --md "$OUT/selfconsist_waveform.md" --json "$OUT/selfconsist_waveform.json"
  echo "   $OUT/selfconsist_waveform.csv and .md (results/ holds the committed copy)"
fi
echo "run.sh: done"
