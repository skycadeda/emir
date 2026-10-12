#!/bin/sh
# Example 03: the open-simulator flow with ngspice, offline, from a clone.
#
#   sh examples/03_ngspice/run.sh [OUT_DIR]   (ngspice on $PATH, or $NGSPICE_BIN)
#
# rcc: simulate the RC netlist (the DSPF itself) and read every branch current.
# The run ends in a FAIL verdict on purpose (README.md works out why, by
# hand). Outputs go to OUT_DIR (the first argument), else $EMIR_EXAMPLE_OUT
# (default: emir_out/ beside this file).
# Exit status: emir's (3 when ngspice is not found); 1 when a value differs from
# expected/summary.json beyond its tolerance.
set -eu
self=$0
while [ -h "$self" ]; do
    dir=$(cd "$(dirname "$self")" && pwd -P)
    link=$(readlink "$self")
    case $link in /*) self=$link ;; *) self=$dir/$link ;; esac
done
here=$(cd "$(dirname "$self")" && pwd -P)
root=$(cd "$here/../.." && pwd -P)
EMIR=${EMIR:-"$root/bin/emir"}
SIM=${EMIR_EXAMPLE_SIM:-ngspice}
out=${1:-${EMIR_EXAMPLE_OUT:-"$here/emir_out"}}
# a relative OUT_DIR is taken from where run.sh was started
case $out in /*) ;; *) out=$(pwd -P)/$out ;; esac
proc="$root/emir/data/sample/process.yaml"     # ILLUSTRATIVE limits, not a PDK's
cd "$here"

echo "== 1. simulate the RC netlist with $SIM (rcc: every branch current)"
"$EMIR" simulate tb_rcc.cir --sim "$SIM" --dspf loadrail.dspf --out "$out/sim/rcc.raw"
echo "== 2. screen it (exit 0: a FAIL verdict is a result, not an error)"
"$EMIR" run loadrail.dspf --process "$proc" --cc-sim "$out/sim/rcc.raw" \
    --period 1e-8 --em-temp 100 --summary-out "$out/rcc.json"
echo "== 3. the summary to relay"
"$EMIR" report --from "$out/rcc.json"
echo "== 4. compare with the golden values (expected/summary.json)"
"${EMIR_PYTHON:-python3}" "$here/../02_xyce/compare.py" "$out" "$here/expected/summary.json"
