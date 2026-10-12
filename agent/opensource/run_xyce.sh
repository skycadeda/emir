#!/bin/sh
# The open-source DSPF flow with Xyce: see emir_open_flow.sh for the options.
#   run_xyce.sh [--net N] --period S [--out DIR] TESTBENCH DSPF PROCESS
exec "$(dirname "$0")/emir_open_flow.sh" xyce "$@"
