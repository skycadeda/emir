#!/usr/bin/env bash
# Example 08: EM/IR of a SKY130 layout on the open-PDK route: magic extracts the
# network (`emir extract --tool magic`), Xyce or ngspice simulates it.
#
#   examples/08_open_layout/run.sh [OUT_DIR]
#
# Needs: Python with numpy, scipy, pyyaml and klayout (pip install -e '.[klayout]'
# from the clone root); PDK_ROOT holding sky130A; a magic that writes resistor
# geometry (the geoout patch: playbook 09 says how to build it); Xyce or
# ngspice (EMIR_EXAMPLE_SIM=ngspice picks ngspice). Without them it says what is
# missing and exits 0.
#
# A. the gate pulsed (three 4 ns periods): rcc, the reference (the DSPF itself
#    simulated, every branch current saved); cc-grid open loop (the CC netlist);
#    cc-grid self-consistent on out, vss and vb (the waveform loop), and on the
#    rails alone;
# B. the operating point the removed `emir layout` simulated, on magic's network.
# compare.py checks every result against expected/results.json (and rcc branch
# by branch against the simulator's own output) and exits non-zero on a
# mismatch. EMIR (default: the clone's bin/emir) and PYTHON can be overridden.
# `sh run.sh` works too: this script needs bash (arrays, pipefail), so a POSIX sh
# hands it over before any bash syntax
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
out="${1:-$here/out}"
emir="${EMIR:-$root/bin/emir}"
python="${PYTHON:-${EMIR_PYTHON:-python3}}"
proc="$root/pdks/sky130.yaml"
mkdir -p "$out"

sim="${EMIR_EXAMPLE_SIM:-}"
if [ -z "$sim" ]; then
    if command -v Xyce >/dev/null 2>&1; then sim=xyce; elif command -v ngspice >/dev/null 2>&1; then sim=ngspice; fi
fi
if [ -z "${PDK_ROOT:-}" ] || [ ! -d "${PDK_ROOT}/sky130A" ] || [ -z "$sim" ]; then
    # the reason goes into OUT_DIR too, so a run that skips still says why there
    echo "== example 08 skipped: it needs PDK_ROOT with sky130A, and Xyce or ngspice" | tee "$out/skipped.txt"
    exit 0
fi

"$python" "$here/make_layouts.py" "$out" >/dev/null

echo "== extract: magic, every resistor's geometry"
set +e
"$emir" extract --tool magic --gds "$out/sink.gds" --cell sink --process "$proc" \
    --out "$out/extract" --format json > "$out/extract.json" 2> "$out/extract.log"
rc=$?
set -e
if [ "$rc" = 3 ]; then
    echo "== example 08 skipped: no magic that writes resistor geometry (EMIR-E643; see $out/extract.log)" | tee "$out/skipped.txt"
    exit 0
fi
[ "$rc" = 0 ] || { cat "$out/extract.log"; exit "$rc"; }
dspf="$out/extract/sink.dspf"
cc="$out/extract/sink.cc.spice"
for t in tran op; do
    for k in rcc cc; do
        netlist="$dspf"; [ "$k" = cc ] && netlist="$cc"
        name="tb_$k.cir"; [ "$t" = op ] && name="tb_op_$k.cir"
        sed -e "s|@PDK_ROOT@|$PDK_ROOT|g" -e "s|@NETLIST@|$netlist|g" "$here/tb_$t.cir.in" > "$out/$name"
    done
done

run() {                  # run <name> <emir run options...>: the run's text in <name>.log
    local name="$1"; shift
    "$emir" run "$dspf" --process "$proc" --summary-out "$out/$name.json" "$@" > "$out/$name.log" 2>&1 \
        || { tail -n 30 "$out/$name.log"; exit 1; }
}

echo "== A: the gate pulsed, $sim"
"$emir" simulate "$out/tb_rcc.cir" --sim "$sim" --dspf "$dspf" --mode rcc --out "$out/sim/rcc.raw" \
    > "$out/sim_rcc.log" 2>&1 || { tail -n 30 "$out/sim_rcc.log"; exit 1; }
run rcc --cc-sim "$out/sim/rcc.raw" --mode rcc --period 4e-9 \
    --viewer "$out/rcc_viewer" --viewer-all --html "$out/html" --gds "$out/sink.gds" --cell sink
"$emir" simulate "$out/tb_cc.cir" --sim "$sim" --dspf "$dspf" --mode cc-grid --probe \
    --out "$out/sim/cc.raw" > "$out/sim_cc.log" 2>&1 || { tail -n 30 "$out/sim_cc.log"; exit 1; }
run open --cc-sim "$out/sim/cc.raw" --mode cc-grid --period 4e-9 --vdd out=1.8,vss=0 \
    --viewer "$out/open_viewer" --viewer-all
run sc --net out,vss,vb --mode cc-grid --self-consistent --sim "$sim" --netlist "$out/tb_cc.cir" \
    --vdd out=1.8,vss=0,vb=1.0 --period 4e-9 --droop-file "$out/sc/droop.inc" \
    --viewer "$out/sc_viewer" --viewer-all
# the loop on the two rails only: the gate's drop is not fed back
run sc_rails --net out,vss --mode cc-grid --self-consistent --sim "$sim" --netlist "$out/tb_cc.cir" \
    --vdd out=1.8,vss=0 --period 4e-9 --droop-file "$out/sc_rails/droop.inc" \
    --viewer "$out/sc_rails_viewer" --viewer-all

echo "== B: the operating point, $sim"
"$emir" simulate "$out/tb_op_rcc.cir" --sim "$sim" --dspf "$dspf" --mode rcc --out "$out/sim/op_rcc.raw" \
    > "$out/sim_op_rcc.log" 2>&1 || { tail -n 30 "$out/sim_op_rcc.log"; exit 1; }
"$emir" simulate "$out/tb_op_cc.cir" --sim "$sim" --dspf "$dspf" --mode cc-grid --probe \
    --out "$out/sim/op_cc.raw" > "$out/sim_op_cc.log" 2>&1 || { tail -n 30 "$out/sim_op_cc.log"; exit 1; }
for net in out vss; do   # one net at a time: vb carries no current at DC
    run "op_rcc_$net" --net "$net" --cc-sim "$out/sim/op_rcc.raw" --mode rcc --period 1e-9
    run "op_cc_$net" --net "$net" --cc-sim "$out/sim/op_cc.raw" --mode cc-grid --period 1e-9
done

"$python" "$here/compare.py" "$out" "$here/expected/results.json"
"$emir" report --from "$out/sc.json" > "$out/report.md"
echo "   comparison: $out/comparison.md  report: $out/report.md  html: $out/html/index.html"
