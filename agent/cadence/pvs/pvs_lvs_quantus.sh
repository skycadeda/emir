#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Vijay Gupta and contributors
# Part of EMIR, the open-source EM/IR screening tool. Not a Cadence-supplied file.
#
# EMIR extraction, Cadence flow: PVS LVS (connectivity and the LVS database)
# -> the MATCH gate -> Quantus -> <cell>.<kind>.dspf.
#
# Status: UNVERIFIED until checked on site. It is tested here with stand-in
# executables only; on the machine that has PVS and Quantus it is verified on
# site by the local agent: the MATCH gate below, then `emir check
# <cell>.<kind>.dspf --process <pdk.yaml>` on the DSPF it writes. `emir extract
# --run` does the same from emir; this script is for a site that runs the flow
# without it (it needs python3 for render_ccl.py).
#
#   PVS_RULES=<the PDK's PVS LVS deck> QRC_TECH_DIR=<the PDK's Quantus dir> \
#   QRC_CORNER=<corner> EXTRACT_TEMP=<C> \
#   sh pvs_lvs_quantus.sh <gds> <cell> <cdl> [workdir] [kind]
#
# kind: r_only (default: the EM/IR grid), c_only_coupled (the CC netlist of a
# big block), rc_coupled (the RC netlist of a small block, for --mode rcc).
# Run it once per kind with the same workdir: a matched LVS newer than the GDS
# and the CDL is reused, so both DSPFs come from one LVS and name nets alike.
#
# Optional: PVS_BIN, QRC_BIN, PYTHON (default python3), SOURCE_CELL (the CDL
# top cell), FORCE_LVS=1 (run LVS again), LVS_REPORT (default
# <cell>.lvsrpt.cls, EMIR's default), LVS_MATCH (an extended regular
# expression the report must hold; default EMIR's MATCH line; both UNVERIFIED
# for your PVS release until the gate passes on site).
#
# Exit status: 0, with the DSPF path on stdout; 2 refused (a setting missing,
# LVS did not match, no DSPF written); 3 a tool not found.
set -eu

die() { echo "pvs_lvs_quantus: $2" >&2; exit "$1"; }
[ $# -ge 3 ] || die 2 "usage: sh pvs_lvs_quantus.sh <gds> <cell> <cdl> [workdir] [kind]"
gds=$1; cell=$2; cdl=$3
work=${4:-emir_out/extract_$cell}
kind=${5:-r_only}
case $kind in
    r_only|c_only_coupled|rc_coupled|rc_decoupled|c_only_decoupled) ;;
    *) die 2 "unknown kind '$kind' (r_only, c_only_coupled, rc_coupled, rc_decoupled, c_only_decoupled)" ;;
esac
[ -f "$gds" ] || die 2 "no GDS file $gds"
[ -f "$cdl" ] || die 2 "no CDL file $cdl"
[ -n "${PVS_RULES:-}" ] || die 2 "set PVS_RULES to the PDK's PVS LVS rule deck"
[ -f "$PVS_RULES" ] || die 2 "PVS_RULES: no file $PVS_RULES"
[ -n "${QRC_TECH_DIR:-}" ] || die 2 "set QRC_TECH_DIR to the PDK's Quantus technology directory"
[ -n "${QRC_CORNER:-}" ] || die 2 "set QRC_CORNER to a corner (a sub-directory of QRC_TECH_DIR)"
[ -d "$QRC_TECH_DIR/$QRC_CORNER" ] || die 2 "no corner directory $QRC_TECH_DIR/$QRC_CORNER"
[ -n "${EXTRACT_TEMP:-}" ] || die 2 "set EXTRACT_TEMP to the extraction temperature in C"
PVS=${PVS_BIN:-pvs}
QRC=${QRC_BIN:-qrc}
PY=${PYTHON:-python3}
command -v "$PVS" >/dev/null 2>&1 || die 3 "pvs not found ($PVS): set PVS_BIN or PATH"
command -v "$QRC" >/dev/null 2>&1 || die 3 "qrc not found ($QRC): set QRC_BIN or PATH"
command -v "$PY" >/dev/null 2>&1 || die 3 "python3 not found: set PYTHON"
here=$(cd "$(dirname "$0")" && pwd)

mkdir -p "$work"
work=$(cd "$work" && pwd)
gds=$(cd "$(dirname "$gds")" && pwd)/$(basename "$gds")
cdl=$(cd "$(dirname "$cdl")" && pwd)/$(basename "$cdl")
report=$work/${LVS_REPORT:-$cell.lvsrpt.cls}
match=${LVS_MATCH:-'Run Result[[:space:]]*:[[:space:]]*MATCH'}

matched() { [ -f "$report" ] && grep -Eq "$match" "$report"; }

# 1. LVS, unless a matched one newer than both inputs is there. -rc_data is
#    what makes the LVS database usable by Quantus; without it LVS passes and
#    the extraction has nothing to read.
if [ "${FORCE_LVS:-0}" != 1 ] && [ -d "$work/svdb" ] && matched &&
   [ "$report" -nt "$gds" ] && [ "$report" -nt "$cdl" ]; then
    echo "pvs_lvs_quantus: reusing the matched LVS in $work/svdb" >&2
else
    # some decks include ./pvs_control_file from the run directory
    ctl=$(dirname "$PVS_RULES")/pvs_control_file
    if [ -f "$ctl" ]; then cp "$ctl" "$work/"; fi
    rm -f "$report"
    (cd "$work" && "$PVS" -lvs -rc_data -ui_data \
        -gds "$gds" -layout_top_cell "$cell" \
        -source_cdl "$cdl" -source_top_cell "${SOURCE_CELL:-$cell}" \
        "$PVS_RULES") > "$work/pvs.log" 2>&1 || true
    # The gate: never extract on connectivity LVS has not verified. PVS's own
    # exit status is not used; the report is.
    matched || die 2 "LVS of $cell did not MATCH: see $report and $work/pvs.log"
fi

# 2. Quantus, with an ABSOLUTE LVS database path (it runs in $work, where a
#    relative path would be resolved a second time and not found).
dspf_name=$cell.$kind.dspf
"$PY" "$here/../quantus/render_ccl.py" --svdb "$work/svdb" --cell "$cell" \
    --tech-dir "$QRC_TECH_DIR" --corner "$QRC_CORNER" --temperature "$EXTRACT_TEMP" \
    --kind "$kind" --out-file "$dspf_name" > "$work/qrc_$kind.ccl"
rm -f "$work/$dspf_name" "$work/qrc_$kind/$dspf_name"
(cd "$work" && "$QRC" -cmd "qrc_$kind.ccl") > "$work/qrc_$kind.stdout" 2>&1 || true
for d in "$work/$dspf_name" "$work/qrc_$kind/$dspf_name"; do
    if [ -f "$d" ]; then echo "$d"; exit 0; fi
done
die 2 "Quantus wrote no $dspf_name: see $work/qrc.$kind.log and $work/qrc_$kind.stdout"
