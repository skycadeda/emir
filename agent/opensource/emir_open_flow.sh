#!/bin/sh
# The whole open-source DSPF flow in one go, for a person at a shell (an agent
# runs the same steps one by one: `emir next --netlist TESTBENCH ...`).
#
#   emir_open_flow.sh SIM [options] TESTBENCH DSPF PROCESS
#   SIM      xyce | ngspice (run_xyce.sh / run_ngspice.sh pass it)
#            The testbench simulates the RC netlist (it includes the DSPF):
#            every branch current is saved and read (rcc, every metric)
#   options  --net N        one net (default: every net)
#            --period S     the block's period, seconds (required: without it
#                           nothing is simulated, EMIR-E428)
#            --out DIR      outputs (default emir_out)
#            --mode rcc     accepted (the one method), and may be left out
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
usage() { sed -n '4,13p' "$self" >&2; exit 2; }
[ $# -ge 1 ] || usage
sim=$1; shift
case $sim in xyce|ngspice) ;; *) usage ;; esac
net=""; period=""; out=emir_out
while [ $# -gt 3 ]; do
    case $1 in
        --net) net=$2; shift 2 ;;
        --mode)
            [ "$2" = rcc ] || { echo "--mode $2: 1.1 has one method of a simulated run, rcc (every branch current of the RC netlist): --mode rcc, or leave --mode out" >&2; exit 2; }
            shift 2 ;;
        --period) period=$2; shift 2 ;;
        --out) out=$2; shift 2 ;;
        *) echo "unknown option $1" >&2; usage ;;
    esac
done
[ $# -eq 3 ] || usage
tb=$1; dspf=$2; proc=$3
if [ -z "$period" ]; then
    # emir simulate runs the testbench's .tran: without --period its run is
    # refused (EMIR-E428), so nothing is simulated
    echo "EMIR-E428: no --period: the simulation is a transient, and rcc averages over whole periods of the block's activity; EMIR never guesses them. Give --period (the block's clock, or the period of the testbench's PULSE or SIN sources); nothing was simulated" >&2
    exit 2
fi
raw="$out/sim/result.raw"
set -- ; [ -n "$net" ] && set -- --net "$net"
echo "== 1/4 emir simulate ($sim, rcc: every branch current)"
"$EMIR" simulate "$tb" --sim "$sim" --dspf "$dspf" "$@" \
    --process "$proc" --out "$raw"
set -- "$@" --period "$period"
echo "== 2/4 emir check"
"$EMIR" check "$dspf" --process "$proc" --cc-sim "$raw" "$@"
echo "== 3/4 emir run"
"$EMIR" run "$dspf" --process "$proc" --cc-sim "$raw" "$@" \
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
