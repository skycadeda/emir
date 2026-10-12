#!/bin/sh
# Example 11: the HSPICE route, offline. HSPICE does not run here: the results
# in inputs/ are SYNTHETIC (make_results.py wrote them with emir's writer, in
# the documented HSPICE ASCII layout, and their CSDF twins). See README.md.
#
#   sh examples/11_hspice_offline/run.sh [OUT_DIR]     (default: out/ beside this file)
#
# 1. emir save-lines --sim hspice prints the lines an HSPICE deck needs (every
#    branch current) and writes the names sidecar (<result>.names);
# 2. emir run reads each result (the .tr0 and its CSDF twin) through its
#    sidecar, rcc;
# 3. the digest of the two runs is compared with expected/summary.json.
#
# EMIR names the emir command (default: emir on $PATH, else bin/emir of this
# clone); it may be several words, e.g. EMIR="python3 -m emir".
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
if [ -z "${EMIR:-}" ]; then
    if command -v emir >/dev/null 2>&1; then EMIR=emir; else EMIR="$root/bin/emir"; fi
fi
out=${1:-$here/out}
dspf=$root/emir/data/sample/rail.dspf
proc=$root/emir/data/sample/process.yaml      # ILLUSTRATIVE limits, not a PDK's
marker=$out/.emir-example-11
if [ -d "$out" ] && [ -n "$(ls -A "$out")" ] && [ ! -f "$marker" ] && [ "$out" != "$here/out" ]; then
    echo "example 11: $out holds files this script did not write; give a new or empty directory" >&2
    exit 2
fi
rm -f "$out"/*.tr0 "$out"/*.csd "$out"/*.names "$out"/*.json "$out"/*.inc
mkdir -p "$out"
: > "$marker"
# the runs' own state directory: no verification record of this machine
# changes what they say (the person's ~/.emir is neither read nor written)
EMIR_STATE_DIR=$out/state
export EMIR_STATE_DIR
rm -rf "$EMIR_STATE_DIR"

echo "== save lines for HSPICE: every branch current"
$EMIR save-lines "$dspf" --sim hspice --prefix XI0. \
    --names-out "$out/rcc.tr0.names" --out "$out/rcc.hspice.inc" --format json \
    > "$out/save_lines_rcc.json"
cp "$here/inputs/rcc.tr0" "$here/inputs/rcc.csd" "$out/"
cp "$out/rcc.tr0.names" "$out/rcc.csd.names"

for fmt in tr0 csd; do
    $EMIR run "$dspf" --process "$proc" --cc-sim "$out/rcc.$fmt" --period 1e-8 \
        --summary-out "$out/rcc_$fmt.json" > "$out/rcc_$fmt.log"
done
$EMIR report --from "$out/rcc_tr0.json"

"${EMIR_PYTHON:-python3}" "$here/compare.py" "$out" "$here/expected/summary.json"
