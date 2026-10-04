#!/usr/bin/env bash
# The chip-level (hierarchical) example, offline: two blocks characterised once,
# the top screened with them black-boxed, the budgets, the back-annotation of
# the two-pin block, and the checks against hand numbers and a flat run.
#
#   examples/05_chip_level/run.sh [OUTDIR]        (default: examples/05_chip_level/out)
#
# Uses `emir` from PATH, else `python3 -m emir` from this clone ($EMIR and
# $PYTHON override). Exits non-zero when a result differs from expected/.
# `sh run.sh` works too: this script needs bash (arrays, pipefail), so a
# POSIX sh hands it over before any bash syntax
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$HERE/out}"
PY="${PYTHON:-python3}"
if ! "$PY" -c "import emir" 2>/dev/null; then
  export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
fi
if [ -n "${EMIR:-}" ]; then
  read -r -a EM <<< "$EMIR"
elif command -v emir >/dev/null 2>&1; then
  EM=(emir)
else
  EM=("$PY" -m emir)
fi
mkdir -p "$OUT"
LOG="$OUT/run.log"
: > "$LOG"
step() { echo "== $*"; }

step "inputs (synthetic, no simulator)"
"$PY" "$HERE/make_inputs.py" "$OUT" >> "$LOG"

step "1. each block once: emir abstract"
"${EM[@]}" abstract "$OUT/adc.dspf" --process "$OUT/process.yaml" --cc-sim "$OUT/adc.raw" \
  --mode cc-grid --period 10e-9 --points 4000 --vdd 1.0 -o "$OUT/adc.abstract.json" \
  --format json > "$OUT/adc.summary.json" 2>> "$LOG"
"${EM[@]}" abstract "$OUT/pll.dspf" --process "$OUT/process.yaml" --cc-sim "$OUT/pll.raw" \
  --mode cc-grid --period 2.5e-9 --points 1000 --vdd 1.0 -o "$OUT/pll.abstract.json" \
  --format json > "$OUT/pll.summary.json" 2>> "$LOG"

step "2. the top, blocks black-boxed: emir check, then emir run --abstract"
TOP=("$OUT/top.dspf" --process "$OUT/process.yaml" --net VDD
     --abstract "$OUT/adc.abstract.json@XADC" --abstract "$OUT/pll.abstract.json@XPLL"
     --place XPLL=40,0,R0 --align sync)
"${EM[@]}" check "${TOP[@]}" --format json > "$OUT/top.check.json" 2>> "$LOG"
"${EM[@]}" run "${TOP[@]}" --budget-mv 15 --port-voltages-out "$OUT/port_voltages.json" \
  --summary-out "$OUT/top.summary.json" --format json > /dev/null 2>> "$LOG"
"${EM[@]}" report --from "$OUT/top.summary.json" > "$OUT/report.md" 2>> "$LOG"

step "3. back-annotation of the two-pin block with the top's pin voltages"
"${EM[@]}" abstract "$OUT/pll.dspf" --process "$OUT/process.yaml" --cc-sim "$OUT/pll.raw" \
  --mode cc-grid --period 2.5e-9 --points 1000 --vdd 1.0 --port-voltages "$OUT/port_voltages.json@XPLL" \
  -o "$OUT/pll.annotated.json" --format json > "$OUT/pll.annotated.summary.json" 2>> "$LOG"

step "4. the ADC firing in bursts, for the alignment modes"
"${EM[@]}" abstract "$OUT/adc.dspf" --process "$OUT/process.yaml" --cc-sim "$OUT/adc_burst.raw" \
  --mode cc-grid --period 10e-9 --points 4000 -o "$OUT/burst.abstract.json" \
  --format json > "$OUT/burst.summary.json" 2>> "$LOG"

step "5. the reference: the flat network with the stock run"
"${EM[@]}" run "$OUT/flat.dspf" --process "$OUT/process.yaml" --net VDD --cc-sim "$OUT/flat.raw" \
  --period 10e-9 --mode cc-grid --format json > "$OUT/flat.summary.json" 2>> "$LOG"

step "6. measured numbers vs hand and vs flat, self-consistency loop"
"$PY" "$HERE/measure.py" "$OUT" | tee -a "$LOG" | tail -n 1

step "7. compare with expected/"
"$PY" "$HERE/compare.py" "$OUT"
