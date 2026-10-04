#!/bin/sh
# Example 12: cc-grid open loop, then self-consistent, against the RCC reference,
# offline, from a clone. Xyce by default; EMIR_EXAMPLE_SIM=ngspice runs ngspice.
#
#   sh examples/12_self_consistent/run.sh [OUT_DIR]   (Xyce on $PATH, or $XYCE_BIN)
#   EMIR_EXAMPLE_SIM=ngspice sh examples/12_self_consistent/run.sh [OUT_DIR]
#
# 1. open loop: the CC testbench (the loads on an ideal rail) with a 0 V probe on
#    each load's pin, superposed through the R grid (cc-grid); --vdd lets the run
#    judge the droop: 9 % of the supply, EMIR-W520, and the self-consistent route
#    planned in its next steps.
# 2. self-consistent: the same CC testbench, split per pin by EMIR (each load on a
#    supply node of its own, fed from the 1.2 V rail through a source at its own
#    droop waveform), simulated pass by pass until the droop the currents produce
#    is the droop they were fed.
# 3. the RCC reference: the RC netlist (the DSPF with its loads), every branch
#    current read.
# The loads are resistors, so the closed loop is the resistive network itself:
# the self-consistent run must land on the rcc one and on README.md's hand
# numbers. Outputs go to OUT_DIR (the first argument), else $EMIR_EXAMPLE_OUT
# (default: emir_out/ beside this file).
# Exit status: emir's (3 when the simulator is not found); 1 when a value
# differs from expected/summary.json beyond its tolerance.
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
SIM=${EMIR_EXAMPLE_SIM:-xyce}
out=${1:-${EMIR_EXAMPLE_OUT:-"$here/emir_out"}}
# a relative OUT_DIR is taken from where run.sh was started
case $out in /*) ;; *) out=$(pwd -P)/$out ;; esac
proc="$root/emir/data/sample/process.yaml"     # ILLUSTRATIVE limits, not a PDK's
cd "$here"

echo "== 1. open loop: the CC testbench with $SIM, a probe on each load's pin (cc-grid)"
"$EMIR" simulate tb_cc.cir --sim "$SIM" --dspf biasrail.dspf --mode cc-grid --probe \
    --out "$out/sim/cc.raw"
"$EMIR" run biasrail.dspf --process "$proc" --net VDD --cc-sim "$out/sim/cc.raw" \
    --mode cc-grid --vdd 1.2 --period 1e-9 --summary-out "$out/open.json"
echo "== 2. self-consistent: the same testbench, each pin at the rail + its droop, pass by pass"
"$EMIR" run biasrail.dspf --process "$proc" --net VDD --mode cc-grid --self-consistent \
    --sim "$SIM" --netlist tb_cc.cir --vdd 1.2 --period 1e-9 \
    --droop-file "$out/sc/droop.inc" --summary-out "$out/sc.json"
echo "== 3. the RCC reference: the RC netlist with $SIM, every branch current (rcc)"
"$EMIR" simulate tb_rcc.cir --sim "$SIM" --dspf biasrail.dspf --mode rcc \
    --out "$out/sim/rcc.raw"
"$EMIR" run biasrail.dspf --process "$proc" --net VDD --cc-sim "$out/sim/rcc.raw" \
    --mode rcc --period 1e-9 --summary-out "$out/rcc.json"
echo "== 4. the summary to relay"
"$EMIR" report --from "$out/sc.json"
echo "== 5. compare with the golden values (expected/summary.json) and the hand numbers"
"${EMIR_PYTHON:-python3}" "$here/compare.py" "$out" "$here/expected/summary.json"
