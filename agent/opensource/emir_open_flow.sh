#!/bin/sh
# The whole open-source DSPF flow in one go, for a person at a shell (an agent
# runs the same steps one by one: `emir next --netlist TESTBENCH ...`).
#
#   emir_open_flow.sh SIM --mode M [options] TESTBENCH DSPF PROCESS
#   SIM      xyce | ngspice (run_xyce.sh / run_ngspice.sh pass it)
#   --mode M rcc | cc-grid, required: the person's choice (`emir next` lists
#            what each costs and how accurate it is). rcc: the testbench
#            simulates the RC netlist (the DSPF); cc-grid: a CC netlist
#   options  --net N        one net (default: every net)
#            --probe        cc-grid: a 0 V probe on each device pin (devices
#                           inside subcircuits, as open PDKs ship them)
#            --period S     the block's period, seconds (default: detected)
#            --out DIR      outputs (default emir_out)
#
# 1. emir simulate  the testbench, with the save lines, to an ASCII result and
#                   its names sidecar (DIR/sim/result.raw, .names)
# 2. emir check     the inputs (exit 2 and coded FAILs if not)
# 3. emir run       EM + IR: DIR/summary.json, DIR/html/, DIR/viewer/emir.lyrdb
# 4. emir report    DIR/report.md, the summary to read (screening, not sign-off)
#
# Exit status: the first failing step's (emir's contract: 2 bad input,
# 3 a tool missing, ...); 0 when all ran, whatever the verdict.
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
usage() { sed -n '4,15p' "$self" >&2; exit 2; }
[ $# -ge 1 ] || usage
sim=$1; shift
case $sim in xyce|ngspice) ;; *) usage ;; esac
net=""; mode=""; probe=""; period=""; out=emir_out
while [ $# -gt 3 ]; do
    case $1 in
        --net) net=$2; shift 2 ;;
        --mode) mode=$2; shift 2 ;;
        --probe) probe=--probe; shift ;;
        --period) period=$2; shift 2 ;;
        --out) out=$2; shift 2 ;;
        *) echo "unknown option $1" >&2; usage ;;
    esac
done
[ $# -eq 3 ] || usage
case $mode in
    rcc|cc-grid) ;;
    *) echo "give --mode rcc or --mode cc-grid: the mode is the person's choice (emir next lists the trade-offs)" >&2; usage ;;
esac
tb=$1; dspf=$2; proc=$3
raw="$out/sim/result.raw"
set -- ; [ -n "$net" ] && set -- --net "$net"
echo "== 1/4 emir simulate ($sim, mode $mode)"
"$EMIR" simulate "$tb" --sim "$sim" --dspf "$dspf" --mode "$mode" $probe "$@" \
    --process "$proc" --out "$raw"
[ -n "$period" ] && set -- "$@" --period "$period"
echo "== 2/4 emir check"
"$EMIR" check "$dspf" --process "$proc" --cc-sim "$raw" --mode "$mode" "$@"
echo "== 3/4 emir run"
"$EMIR" run "$dspf" --process "$proc" --cc-sim "$raw" --mode "$mode" "$@" \
    --summary-out "$out/summary.json" --html "$out/html" --viewer "$out/viewer"
echo "== 4/4 emir report -> $out/report.md"
"$EMIR" report --from "$out/summary.json" > "$out/report.md"
cat "$out/report.md"
echo
echo "HTML:    $out/html/index.html"
if [ -f "$out/viewer/emir.lyrdb" ]; then
    echo "KLayout: klayout <your layout.gds> -m $out/viewer/emir.lyrdb"
    echo "         (no GDS at hand: python3 $here/dspf_preview_gds.py $dspf -o $out/preview.gds)"
fi
