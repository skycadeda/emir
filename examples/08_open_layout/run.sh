#!/usr/bin/env bash
# Example 08: EM/IR of a SKY130 layout on the open-PDK route: magic extracts the
# network (`emir extract --tool magic`), Xyce or ngspice simulates it.
#
#   examples/08_open_layout/run.sh [OUT_DIR]
#
# Needs: Python with numpy, scipy, pyyaml and klayout (pip install -e '.[klayout]'
# from the clone root); PDK_ROOT holding sky130A; a magic that writes resistor
# geometry (the geoout patch: docs/process-files.md says how to build it); Xyce or
# ngspice (EMIR_EXAMPLE_SIM=ngspice picks ngspice). Without them it says what is
# missing and exits 0.
#
# A. the gate pulsed (four 4 ns periods): rcc (the DSPF itself simulated,
#    every branch current saved: every metric) over the last three; without
#    --period the run is refused (EMIR-E428: a transient needs the block's
#    period), and with a period 10 % off (3.6 ns) too (EMIR-E429: its last two
#    periods differ), with no number;
# B. the DC bias, held over a short transient with every source at DC, on
#    magic's network (rcc, one net at a time; --period 1e-9 averages its last
#    three 1 ns spans: any period that fits twice gives the same numbers).
# compare.py checks every result against expected/results.json, rcc branch by
# branch against the simulator's own output, and rcc's IR against the
# simulator's own node voltages (one more short run of A's deck, printing them),
# and exits non-zero on a mismatch. EMIR (default: the clone's bin/emir) and PYTHON can be overridden.
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
for t in tran op; do
    name="tb_rcc.cir"; [ "$t" = op ] && name="tb_op_rcc.cir"
    sed -e "s|@PDK_ROOT@|$PDK_ROOT|g" -e "s|@NETLIST@|$dspf|g" "$here/tb_$t.cir.in" > "$out/$name"
done

run() {                  # run <name> <emir run options...>: the run's text in <name>.log
    local name="$1"; shift
    "$emir" run "$dspf" --process "$proc" --summary-out "$out/$name.json" "$@" > "$out/$name.log" 2>&1 \
        || { tail -n 30 "$out/$name.log"; exit 1; }
}

echo "== A: the gate pulsed, $sim"
"$emir" simulate "$out/tb_rcc.cir" --sim "$sim" --dspf "$dspf" --out "$out/sim/rcc.raw" \
    > "$out/sim_rcc.log" 2>&1 || { tail -n 30 "$out/sim_rcc.log"; exit 1; }
run rcc --cc-sim "$out/sim/rcc.raw" --period 4e-9 \
    --viewer "$out/rcc_viewer" --viewer-all --html "$out/html" --gds "$out/sink.gds" --cell sink
refused() {              # refused <name> <emir run options...>: exit 2, no number
    local name="$1" rc; shift
    set +e
    "$emir" run "$dspf" --process "$proc" --cc-sim "$out/sim/rcc.raw" "$@" \
        --summary-out "$out/$name.json" > "$out/$name.log" 2>&1
    rc=$?
    set -e
    [ "$rc" = 2 ] || { tail -n 30 "$out/$name.log"; echo "run.sh: exit $rc, not 2 (refused)"; exit 1; }
}
echo "== A without --period: refused (EMIR-E428), no number"
refused rcc_no_period
echo "== A with a period 10 % off (3.6 ns): refused (EMIR-E429), no number"
refused rcc_near_period --period 3.6e-9

echo "== B: the DC bias, held over a short transient, $sim"
"$emir" simulate "$out/tb_op_rcc.cir" --sim "$sim" --dspf "$dspf" --out "$out/sim/op_rcc.raw" \
    > "$out/sim_op_rcc.log" 2>&1 || { tail -n 30 "$out/sim_op_rcc.log"; exit 1; }
for net in out vss; do   # one net at a time: vb carries no current at DC
    run "op_rcc_$net" --net "$net" --cc-sim "$out/sim/op_rcc.raw" --period 1e-9
done

"$python" "$here/compare.py" "$out" "$here/expected/results.json"
"$emir" report --from "$out/rcc.json" > "$out/report.md"
echo "   comparison: $out/comparison.md  report: $out/report.md  html: $out/html/index.html"
