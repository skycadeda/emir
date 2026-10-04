#!/bin/sh
# Example 09: the Cadence flow, offline. No Cadence tool runs here: `emir
# extract` prints its plan (PVS LVS, the MATCH gate, Quantus) without --run,
# `emir save-lines` writes the Spectre include file, and the RCC testbench
# template is filled. See README.md.
#
#   sh examples/09_cadence_flow/run.sh [OUT_DIR]      (default: out/ beside this file)
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
sample=$root/emir/data/sample/rail.dspf
# Never delete a directory the person chose: a non-empty OUT_DIR is used only
# when this script wrote it (its marker), or it is the example's own out/.
marker=$out/.emir-example-09
if [ -d "$out" ] && [ -n "$(ls -A "$out")" ] && [ ! -f "$marker" ] && [ "$out" != "$here/out" ]; then
    echo "example 09: $out holds files this script did not write; give a new or empty directory" >&2
    exit 2
fi
# remove only what an earlier run of this script wrote
rm -rf "$out/inputs" "$out/pdk" "$out/work" "$out/tb"
rm -f "$out"/plan_*.json "$out/site.yaml" "$out/save_lines.json" "$out/summary.json"
mkdir -p "$out/inputs" "$out/pdk/qrc/typ" "$out/tb"
: > "$marker"

# Stand-in inputs: the dry run only checks that the GDS and the CDL exist.
printf 'stand-in for the layout of railblock (the dry run does not read it)\n' > "$out/inputs/railblock.gds"
printf '.SUBCKT railblock VDD\n.ENDS\n' > "$out/inputs/railblock.cdl"
# Stand-in PDK decks, named the way a site file names the real ones.
printf '// stand-in PVS LVS rule deck\n' > "$out/pdk/lvs.rul"

# 1. The plan with nothing configured: what is missing, and where to set it.
env -u PVS_RULES -u QRC_TECH_DIR -u QRC_CORNER -u EMIR_EXTRACT_TEMP -u PVS_BIN -u QRC_BIN \
    EMIR_SITE=/dev/null \
    $EMIR extract --gds "$out/inputs/railblock.gds" --cdl "$out/inputs/railblock.cdl" \
    --cell railblock --kind r_only --work "$out/work" --format json > "$out/plan_unconfigured.json"

# 2. A site file with the decks, a corner and a temperature (tools: whatever
#    $PATH has; here, most likely none), then the plans of both extractions
#    of the big-block flow: the R-only grid and the C-only CC netlist, one LVS.
cat > "$out/site.yaml" <<YAML
pvs_lvs_rules: $out/pdk/lvs.rul
qrc_tech_dir: $out/pdk/qrc
qrc_corner: typ
extract_temperature: 25
YAML
for kind in r_only c_only_coupled; do
    env -u PVS_RULES -u QRC_TECH_DIR -u QRC_CORNER -u EMIR_EXTRACT_TEMP \
        $EMIR extract --gds "$out/inputs/railblock.gds" --cdl "$out/inputs/railblock.cdl" \
        --cell railblock --kind "$kind" --work "$out/work" --site "$out/site.yaml" \
        --format json > "$out/plan_$kind.json"
done

# 3. The Spectre save lines for the testbench in the person's mode (rcc here: the
#    17-node sample, every branch current), with the testbench's instance name,
#    written to the file the testbench includes.
$EMIR save-lines "$sample" --mode rcc --prefix XDUT. --out "$out/tb/emir_save.scs" \
    --format json > "$out/save_lines.json"

# 4. The RCC testbench template, filled (the models line is a placeholder:
#    point it at your PDK's Spectre models).
sed -e "s|\${MODELS}|/path/to/your/pdk/models.scs|" -e 's|\${SECTION}|tt|' \
    -e "s|\${DSPF}|$sample|" -e 's|\${PORTS}|VDD|' -e 's|\${CELL}|railblock|' \
    -e 's|\${VDD}|1.2|' -e 's|\${VDD_NET}|VDD|' -e 's|\${TSTOP}|10n|' \
    "$root/agent/cadence/spectre/tb_rcc.scs.tmpl" > "$out/tb/tb_rcc.scs"

# 5. The digest, compared with expected/summary.json (exit 1 on a difference).
"${EMIR_PYTHON:-python3}" "$here/summarize.py" "$out"
echo "example 09: wrote $out/summary.json"
"${EMIR_PYTHON:-python3}" "$here/compare.py" "$out/summary.json" "$here/expected/summary.json"
